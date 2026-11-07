import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if os.name != "nt":
    spec = importlib.util.spec_from_file_location("release_agent", Path(__file__).resolve().parents[1] / "deploy" / "release.py")
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)


@unittest.skipIf(os.name == "nt", "The VM release agent uses Linux filesystem locks")
class ReleaseAgentTests(unittest.TestCase):
    def test_unit_switch_preserves_mount_network_and_cookie_configuration(self):
        original = (b"[Service]\nExecStartPre=/usr/bin/mountpoint -q /srv/wing-night/data\n"
                    b"ExecStart=/usr/bin/docker run --name wing-night -p 127.0.0.1:8040:8040 -v /srv/wing-night/data:/data -e WING_SECURE_COOKIES=1 wing-night:old\n"
                    b"ExecStop=/usr/bin/docker stop -t 20 wing-night\nRestart=always\n")
        drain, candidate = agent.service_units(original, "wing-night:" + "a" * 40)
        self.assertIn(b"wing-night:old\n", drain)
        self.assertIn(b"wing-night:" + b"a" * 40 + b"\nExecStop=", candidate)
        self.assertIn(b"-v /srv/wing-night/data:/data -e WING_SECURE_COOKIES=1", candidate)
        self.assertIn(b"mountpoint -q /srv/wing-night/data", candidate)
        self.assertIn(b"wing-release --stop", candidate)

    def test_unknown_configuration_fails_closed(self):
        with self.assertRaises(RuntimeError):
            agent.service_units(b"ExecStart=unexpected\n", "wing-night:new")

    def test_rollback_pins_actual_image_and_remains_updatable(self):
        original = (b"ExecStart=/usr/bin/docker run --name wing-night wing-night:old\n"
                    b"ExecStop=/usr/bin/docker stop -t 20 wing-night\n")
        actual = "sha256:" + "b" * 64
        _, rollback = agent.service_units(original, actual)
        self.assertIn(actual.encode(), rollback)
        self.assertNotIn(b"wing-night:old", rollback)
        _, candidate = agent.service_units(rollback, "wing-night:" + "a" * 40)
        self.assertIn(b"wing-night:" + b"a" * 40, candidate)

    def test_graceful_stop_signals_before_waiting(self):
        with patch.object(agent, "run", side_effect=["true", "", "false"]) as command:
            agent.stop_container()
        self.assertEqual(command.call_args_list[1].args, ("docker", "kill", "--signal", "SIGINT", "wing-night"))

    def exercise_switch(self, fail_health=False):
        revision, count, archive = "a" * 40, 17, b"checked image archive"
        old_image, new_image = "sha256:" + "b" * 64, "sha256:" + "c" * 64
        metadata = {"version": "v0.17", "revision": revision, "commit_count": count}
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory) / "unit"
            unit.write_bytes(b"ExecStart=/usr/bin/docker run --name wing-night wing-night:old\nExecStop=/usr/bin/docker stop -t 20 wing-night\n")
            real_open = open
            def local_open(path, *args, **kwargs):
                return real_open(Path(directory) / "lock" if path == "/run/wing-night-release.lock" else path, *args, **kwargs)
            def command(*args):
                calls.append(args)
                if args[:2] == ("findmnt", "-rn"):
                    return agent.UUID
                if args[:2] == ("systemctl", "is-active"):
                    return "active"
                if args[:2] == ("docker", "inspect"):
                    return new_image if revision.encode() in unit.read_bytes() else old_image
                if args[:2] == ("docker", "run"):
                    return json.dumps(metadata)
                return ""
            def response(request, **_):
                url = request.full_url
                payload = (json.dumps({"sha": revision}).encode() if "api.github.com" in url else
                           json.dumps({"access_token": "disposable-test-token"}).encode() if "169.254" in url else archive)
                return io.BytesIO(payload)
            with patch.object(agent, "STATE", Path(directory) / "state"), patch.object(agent, "UNIT", unit), \
                    patch("builtins.open", side_effect=local_open), patch.object(agent, "run", side_effect=command), \
                    patch.object(agent, "urlopen", side_effect=response), patch.object(agent, "rooms", return_value={"TEST": 1}), \
                    patch.object(agent.signal, "signal"), patch.object(agent, "datetime", wraps=agent.datetime) as clock, \
                    patch.object(agent, "health", side_effect=RuntimeError("candidate unhealthy") if fail_health else None, return_value=metadata), \
                    patch.object(agent, "rollback_health"), \
                    patch("sys.argv", ["wing-release", revision, str(count), hashlib.sha256(archive).hexdigest()]):
                clock.now.return_value = agent.END.replace(day=7)
                if fail_health:
                    with self.assertRaisesRegex(RuntimeError, "candidate unhealthy"):
                        agent.main()
                    self.assertIn(old_image.encode(), unit.read_bytes())
                    self.assertFalse((Path(directory) / "state" / "current.json").exists())
                else:
                    agent.main()
                    evidence = json.loads((Path(directory) / "state" / "current.json").read_text())
                    self.assertEqual(evidence["previous_image"], old_image)
                    self.assertEqual(evidence["image"], new_image)
            backup = calls.index(("systemctl", "start", "wing-backup.service"))
            stop = calls.index(("systemctl", "stop", "wing-night.service"))
            start = calls.index(("systemctl", "start", "wing-night.service"))
            self.assertLess(backup, stop)
            self.assertLess(stop, start)

    def test_successful_switch_backs_up_and_stops_before_start(self):
        self.exercise_switch()

    def test_failed_candidate_rolls_back_pinned_image_without_restoring_database(self):
        self.exercise_switch(fail_health=True)


if __name__ == "__main__":
    unittest.main()

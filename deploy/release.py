#!/usr/bin/env python3
"""Trusted VM release agent: serialized, backed up, single-writer switches."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

DATA = Path("/srv/wing-night/data")
UNIT = Path("/etc/systemd/system/wing-night.service")
STATE = Path("/var/lib/wing-night/releases")
UUID = "1013f458-0eed-4fa9-a9fc-2efbd0c3328d"
END = datetime(2026, 11, 9, 13, tzinfo=timezone.utc)


def run(*args):
    return subprocess.check_output(args, text=True, timeout=180).strip()


def rooms():
    with closing(sqlite3.connect((DATA / "wing-night.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        return dict(db.execute("SELECT code,generation FROM rooms"))


def health(revision, count):
    last_error = "No response"
    for _ in range(30):
        try:
            with urlopen("http://127.0.0.1:8040/api/version", timeout=2) as response:
                release = json.load(response)
            if release == {"version": f"v0.{count}", "revision": revision, "commit_count": count}:
                with urlopen("http://127.0.0.1:8040/", timeout=2) as response:
                    if response.status == 200:
                        return release
        except (OSError, ValueError) as error:
            last_error = str(error)
        time.sleep(1)
    raise RuntimeError("Candidate failed bounded health/version check: " + last_error)


def stop_container():
    if run("docker", "inspect", "wing-night", "--format", "{{.State.Running}}") != "true":
        return
    run("docker", "kill", "--signal", "SIGINT", "wing-night")
    for _ in range(20):
        if run("docker", "inspect", "wing-night", "--format", "{{.State.Running}}") == "false":
            return
        time.sleep(1)
    print("Graceful drain timed out; enforcing bounded container stop.", flush=True)
    run("docker", "stop", "-t", "5", "wing-night")


def rollback_health():
    for _ in range(30):
        try:
            with urlopen("http://127.0.0.1:8040/", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError("Previous-image rollback failed bounded health check")


def service_units(original, candidate):
    drain, stop_matches = re.subn(
        rb"(?m)^ExecStop=[^\r\n]+$",
        b"ExecStop=/usr/bin/python3 /usr/local/sbin/wing-release --stop", original)
    replacement, matches = re.subn(
        rb"(?m)^(ExecStart=.* )(?:wing-night:[^\s]+|sha256:[0-9a-f]{64})[^\S\r\n]*$",
        lambda match: match[1] + candidate.encode(), drain)
    if stop_matches != 1 or matches != 1:
        raise RuntimeError("Unexpected service configuration; refusing to replace it")
    return drain, replacement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("revision", nargs="?")
    parser.add_argument("count", type=int, nargs="?")
    parser.add_argument("checksum", nargs="?")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        stop_container()
        return
    def interrupted(*_):
        raise TimeoutError("Release execution interrupted; attempting safe rollback")
    signal.signal(signal.SIGTERM, interrupted)
    if not args.revision or not re.fullmatch("[0-9a-f]{40}", args.revision) or args.count is None or args.count < 1 or not args.checksum or not re.fullmatch("[0-9a-f]{64}", args.checksum):
        raise ValueError("Invalid release identity")
    if datetime.now(timezone.utc) >= END:
        raise RuntimeError("Event shutdown deadline reached; releases cannot revive this event")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open("/run/wing-night-release.lock", "w") as lock:
        for _ in range(180):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(1)
        else:
            raise RuntimeError("Another release owns the deployment lock")
        if datetime.now(timezone.utc) >= END:
            raise RuntimeError("Event shutdown deadline reached while waiting for release lock")
        run("mountpoint", "-q", str(DATA))
        if run("findmnt", "-rn", "-o", "UUID", "--target", str(DATA)) != UUID:
            raise RuntimeError("Dedicated data disk UUID mismatch")
        if run("systemctl", "is-active", "wing-night.service") != "active":
            raise RuntimeError("Existing service is not active; explicit recovery is required")
        with urlopen(Request("https://api.github.com/repos/DanyoGu/wing-night/commits/main",
                             headers={"User-Agent": "wing-night-release"}), timeout=20) as response:
            current_main = json.load(response)["sha"]
        if current_main != args.revision:
            raise RuntimeError("Superseded revision is not current GitHub main")
        old_unit = UNIT.read_bytes()
        old_image = run("docker", "inspect", "wing-night", "--format", "{{.Image}}")
        _, rollback_unit = service_units(old_unit, old_image)
        candidate = "wing-night:" + args.revision
        with tempfile.TemporaryDirectory(dir=STATE, prefix="staging-") as directory:
            archive = Path(directory) / "image.tgz"
            with urlopen(Request(
                    "http://169.254.169.254/metadata/identity/oauth2/token"
                    "?api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F",
                    headers={"Metadata": "true"}), timeout=20) as response:
                token = json.load(response)["access_token"]
            url = ("https://sthotones20261106.blob.core.windows.net/releases/"
                   + args.revision + "/image.tgz")
            with urlopen(Request(url, headers={"Authorization": "Bearer " + token,
                                               "x-ms-version": "2023-11-03"}), timeout=45) as response:
                with archive.open("wb") as destination:
                    shutil.copyfileobj(response, destination)
            checksum = hashlib.sha256()
            with archive.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    checksum.update(chunk)
            if checksum.hexdigest() != args.checksum:
                raise RuntimeError("Artifact checksum mismatch")
            run("docker", "load", "--input", str(archive))
            metadata = json.loads(run("docker", "run", "--rm", "--network", "none",
                                      "--read-only", "--entrypoint", "python", candidate,
                                      "-c", "import json; print(open('/app/release.json').read())"))
            expected = {"version": f"v0.{args.count}", "revision": args.revision, "commit_count": args.count}
            if metadata != expected:
                raise RuntimeError("Image metadata does not match full-history release identity")
            # A successful off-VM SQLite-aware snapshot is mandatory before downtime.
            run("systemctl", "start", "wing-backup.service")
            if datetime.now(timezone.utc) >= END:
                raise RuntimeError("Shutdown deadline reached before live switch")
            prior_rooms = rooms()
            drain_unit, replacement = service_units(old_unit, candidate)
            switched = False
            try:
                switched = True
                UNIT.write_bytes(drain_unit)
                run("systemctl", "daemon-reload")
                run("systemctl", "stop", "wing-night.service")
                # No new container starts until the existing SQLite writer has stopped.
                UNIT.write_bytes(replacement + b"\n")
                run("systemctl", "daemon-reload")
                run("systemctl", "start", "wing-night.service")
                release = health(args.revision, args.count)
                after_rooms = rooms()
                if any(code not in after_rooms or after_rooms[code] < generation
                       for code, generation in prior_rooms.items()):
                    raise RuntimeError("Existing event identities/generations were lost")
                evidence = {**release, "image": run("docker", "inspect", "wing-night", "--format", "{{.Image}}"),
                            "artifact_sha256": args.checksum, "previous_image": old_image,
                            "database_path": str(DATA / "wing-night.sqlite"), "data_mount_uuid": UUID,
                            "existing_rooms_preserved": len(prior_rooms),
                            "deployed_utc": datetime.now(timezone.utc).isoformat()}
                (STATE / "current.json").write_text(json.dumps(evidence, indent=2))
                print(json.dumps(evidence), flush=True)
            except Exception:
                if switched:
                    run("systemctl", "stop", "wing-night.service")
                    UNIT.write_bytes(rollback_unit)
                    run("systemctl", "daemon-reload")
                    if datetime.now(timezone.utc) >= END:
                        raise RuntimeError("Shutdown deadline reached; refusing to restart during retirement")
                    run("systemctl", "start", "wing-night.service")
                    rollback_health()
                    print("Rolled back the previous image/configuration; database was not restored or reset.",
                          flush=True)
                raise
            keep = {old_image, evidence["image"]}
            for image in run("docker", "images", "wing-night", "--format", "{{.Repository}}:{{.Tag}}").splitlines():
                if not image.startswith("wing-night:") or image.endswith(":<none>"):
                    continue
                full = run("docker", "image", "inspect", image, "--format", "{{.Id}}")
                if full not in keep:
                    run("docker", "image", "rm", image)


if __name__ == "__main__":
    main()

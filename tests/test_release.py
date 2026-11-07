import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch
from urllib.request import urlopen

spec = importlib.util.spec_from_file_location("release_backend", Path(__file__).resolve().parents[1] / "server.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class ReleaseMetadataTests(unittest.TestCase):
    def test_unbuilt_local_source_is_development(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backend, "ROOT", Path(directory)):
            self.assertEqual(backend.release_info(), {"version": "v0.0", "revision": "development", "commit_count": 0})

    def test_exact_full_history_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backend, "ROOT", Path(directory)):
            expected = {"version": "v0.152", "revision": "a" * 40, "commit_count": 152}
            (Path(directory) / "release.json").write_text(json.dumps(expected))
            self.assertEqual(backend.release_info(), expected)

    def test_corrupt_metadata_fails_instead_of_claiming_a_version(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backend, "ROOT", Path(directory)):
            path = Path(directory) / "release.json"
            for data in (
                {"version": "v0.12", "revision": "a" * 40, "commit_count": 13},
                {"version": "v0.1", "revision": "queued", "commit_count": 1},
                {"version": "v0.True", "revision": "a" * 40, "commit_count": True},
            ):
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    backend.release_info()

    def test_shutdown_drains_an_in_flight_http_request(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        clients, replies = [], []
        server_type = backend.ThreadingHTTPServer
        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                entered.set()
                release.wait(3)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"completed")
                finished.set()
            def log_message(self, *_):
                pass
        def construct(*_):
            http = server_type(("127.0.0.1", 0), SlowHandler)
            serve = http.serve_forever
            def interrupted_serve():
                threading.Thread(target=lambda: serve(poll_interval=0.01), daemon=True).start()
                def request():
                    with urlopen(f"http://127.0.0.1:{http.server_port}/", timeout=3) as response:
                        replies.append(response.read())
                client = threading.Thread(target=request)
                clients.append(client)
                client.start()
                self.assertTrue(entered.wait(2))
                threading.Timer(0.2, release.set).start()
                http.shutdown()
                raise KeyboardInterrupt
            http.serve_forever = interrupted_serve
            return http
        try:
            with tempfile.TemporaryDirectory() as directory, \
                    patch("sys.argv", ["server.py", "--port", "0", "--db", str(Path(directory) / "test.sqlite")]), \
                    patch.object(backend, "ThreadingHTTPServer", side_effect=construct), \
                    patch.object(backend, "Store"), patch.object(backend.signal, "signal"):
                backend.main()
                self.assertTrue(finished.is_set(), "server_close must wait for the active handler, not abandon daemon workers")
        finally:
            release.set()
            for client in clients:
                client.join(3)
        self.assertEqual(replies, [b"completed"])


if __name__ == "__main__":
    unittest.main()

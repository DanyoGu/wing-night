import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()

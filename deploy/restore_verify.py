#!/usr/bin/env python3
"""Download and verify a backup on a separate machine, without printing tokens."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--room")
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--blob")
    parser.add_argument("--checksum")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = json.loads(subprocess.check_output(
        ["az.cmd", "account", "get-access-token", "--resource", "https://storage.azure.com/"],
        text=True))["accessToken"]

    def download(name):
        with urlopen(Request("https://sthotones20261106.blob.core.windows.net/backups/" + name,
                             headers={"Authorization": "Bearer " + token,
                                      "x-ms-version": "2023-11-03"}), timeout=30) as response:
            return response.read()

    if args.blob:
        if not args.checksum:
            raise RuntimeError("A checksum is required for an explicitly selected snapshot")
        manifest = {"blob": args.blob, "sha256": args.checksum, "integrity": "ok"}
    else:
        manifest = json.loads(download("latest.json"))
    content = download(manifest["blob"])
    assert hashlib.sha256(content).hexdigest() == manifest["sha256"]
    with tempfile.TemporaryDirectory(prefix="wing-restore-") as directory:
        restored = Path(directory) / "restored.sqlite"
        restored.write_bytes(content)
        with closing(sqlite3.connect(restored)) as db:
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            if args.empty:
                assert db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
                rows, totals = [], [0, 0]
            else:
                if not args.room:
                    raise RuntimeError("The validation room is required")
                rows = db.execute("SELECT v.grades FROM votes v JOIN members m ON m.id=v.member "
                                  "WHERE m.room=? AND m.role='judge'", (args.room,)).fetchall()
                assert len(rows) == 180
                totals = [sum(sum(json.loads(row[0])[p * 2:p * 2 + 2]) for row in rows) for p in range(2)]
                assert totals == [1080, 1080]
        # A second reopen confirms this is a usable restored database, not just a hash match.
        with closing(sqlite3.connect(restored)) as db:
            if args.empty:
                assert db.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 0
            else:
                assert db.execute("SELECT phase FROM rooms WHERE code=?", (args.room,)).fetchone()[0] == "finished"
    evidence = {**manifest, "off_vm_restore": "ok", "restored_votes": len(rows),
                "restored_test_totals": totals, "validation_room": args.room}
    args.output.write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()

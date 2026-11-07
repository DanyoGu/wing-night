#!/usr/bin/env python3
"""Consistent SQLite backups and privacy-filtered event reports."""
import argparse
import csv
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


def request(url, method="GET", data=None, headers=None):
    for attempt in range(4):
        try:
            with urlopen(Request(url, data=data, headers=headers or {}, method=method),
                         timeout=30) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("Request retries exhausted")


def storage_token():
    url = ("http://169.254.169.254/metadata/identity/oauth2/token"
           "?api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F")
    return json.loads(request(url, headers={"Metadata": "true"}))["access_token"]


def upload(account, container, name, data, token, content_type):
    return request(
        f"https://{account}.blob.core.windows.net/{container}/{quote(name, safe='/')}",
        "PUT", data,
        {"Authorization": "Bearer " + token, "x-ms-version": "2023-11-03",
         "x-ms-blob-type": "BlockBlob", "Content-Type": content_type})


def prune_backups(account, token):
    cutoff = datetime.fromtimestamp(time.time() - 86400, timezone.utc).strftime("%Y%m%dT%H%M%S")
    headers = {"Authorization": "Bearer " + token, "x-ms-version": "2023-11-03"}
    listing = ET.fromstring(request(
        f"https://{account}.blob.core.windows.net/backups?restype=container&comp=list"
        "&prefix=snapshots/&maxresults=5000", headers=headers))
    if listing.findtext("NextMarker"):
        raise RuntimeError("Backup listing exceeded retention bound")
    expired = [name.text for name in listing.findall("./Blobs/Blob/Name")
               if name.text.startswith("snapshots/") and
               name.text.endswith(".sqlite") and name.text[10:] < cutoff]
    for name in expired[:100]:
        request(f"https://{account}.blob.core.windows.net/backups/{quote(name, safe='/')}",
                "DELETE", headers=headers)


def snapshot(source, destination):
    if not source.is_file():
        raise RuntimeError("Database is missing; refusing to create an empty replacement")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=15)) as live:
        with closing(sqlite3.connect(destination)) as copy:
            live.backup(copy, pages=128, sleep=0.05)
            if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Snapshot failed SQLite integrity_check")
            if copy.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Snapshot failed foreign_key_check")


def csv_cell(value):
    value = str(value)
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def render_report(path, captured_at, excluded):
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        rooms = [dict(room) for room in db.execute("SELECT * FROM rooms ORDER BY code")
                 if room["code"] not in excluded]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["snapshot_utc", "room", "phase", "active_round",
                         "voting_open", "judge", "round", "daniel_answer",
                         "daniel_handling_heat", "joicee_answer", "joicee_handling_heat",
                         "daniel_points", "joicee_points"])
        sections = []
        summaries = []
        for room in rooms:
            counts = db.execute(
                "SELECT COUNT(*) FROM members WHERE room=? AND role='judge'",
                (room["code"],)).fetchone()[0]
            rounds = [[0, 0, 0] for _ in range(10)]
            votes = []
            active_round = room["round"] + 1 if room["phase"] != "waiting" else ""
            for row in db.execute(
                    "SELECT m.name,v.round,v.grades FROM votes v JOIN members m "
                    "ON m.id=v.member WHERE m.room=? AND m.role='judge' ORDER BY v.round,m.name",
                    (room["code"],)):
                grades = json.loads(row["grades"])
                if (not 0 <= row["round"] < 10 or len(grades) != 4 or
                        any(type(g) is not int or not 1 <= g <= 5 for g in grades)):
                    raise RuntimeError("Invalid stored vote; refusing misleading report")
                points = [sum(grades[:2]), sum(grades[2:])]
                votes.append({"judge": row["name"], "round": row["round"], "grades": grades})
                rounds[row["round"]][0] += points[0]
                rounds[row["round"]][1] += points[1]
                rounds[row["round"]][2] += 1
                writer.writerow([captured_at, room["code"], room["phase"],
                                 active_round, bool(room["voting"]),
                                 csv_cell(row["name"]), row["round"] + 1,
                                 *grades, *points])
            totals = [sum(r[p] for r in rounds) for p in range(2)]
            summary = {**room, "judges": counts, "totals": totals, "rounds": rounds,
                       "votes": votes}
            summaries.append(summary)
            rows = "".join(
                f"<tr><td>{i + 1}</td><td>{r[0]}</td><td>{r[1]}</td>"
                f"<td>{r[2]}/{counts}</td></tr>" for i, r in enumerate(rounds))
            history = "".join(
                "<li>" + html.escape(f"{r['created_at']} UTC: {r['name']} - {r['action']}")
                + "</li>" for r in db.execute(
                    "SELECT c.created_at,m.name,c.action FROM roster_changes c "
                    "JOIN members m ON m.id=c.member WHERE c.room=? ORDER BY c.id",
                    (room["code"],)))
            sections.append(
                f"<h2>Room {html.escape(room['code'])}</h2>"
                f"<p>Phase: {html.escape(room['phase'])}; active round: {active_round or 'not started'}; "
                f"voting open: {bool(room['voting'])}; active judges: {counts}.</p>"
                f"<p>Recorded totals: Daniel {totals[0]}, Joicee {totals[1]}. "
                f"Maximum per competitor with this roster: {counts * 100}.</p>"
                "<table border='1'><tr><th>Round</th><th>Daniel</th><th>Joicee</th>"
                "<th>Submissions</th></tr>" + rows + "</table>"
                "<h3>Roster changes</h3><ul>" + history + "</ul>")
        body = (
            "<!doctype html><html><body><h1>Wing Night judging results</h1>"
            f"<p>Consistent database snapshot completed at {html.escape(captured_at)}.</p>"
            "<p>These are recorded scores, not forecasts. An unfinished room is not a "
            "final result. Voting is not frozen by this report; subsequent edits or "
            "submissions are not included. Each competitor is graded on the answer "
            "and handling the heat (1-5 each). Validation rooms are excluded. Session "
            "cookies, token hashes and member IDs are never exported.</p>"
            + ("".join(sections) if sections else "<p>No non-validation events exist.</p>")
            + "</body></html>")
        return body.encode(), output.getvalue().encode("utf-8-sig"), summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup", "export"))
    parser.add_argument("--db", type=Path, default=Path("/srv/wing-night/data/wing-night.sqlite"))
    parser.add_argument("--account", default=os.environ.get("WING_STORAGE_ACCOUNT"))
    parser.add_argument("--excluded", type=Path,
                        default=Path("/etc/wing-night/validation-rooms.json"))
    parser.add_argument("--local-output", type=Path)
    args = parser.parse_args()
    if not args.local_output and not os.path.ismount(args.db.parent):
        raise RuntimeError("Data directory is not a mounted disk")
    with tempfile.TemporaryDirectory(prefix="wing-snapshot-") as directory:
        copy = Path(directory) / "snapshot.sqlite"
        snapshot(args.db.resolve(), copy)
        captured_at = datetime.now(timezone.utc).isoformat()
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        content = copy.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        manifest = {"snapshot_utc": captured_at, "sha256": checksum,
                    "bytes": len(content), "integrity": "ok"}
        if args.action == "backup":
            token = storage_token()
            manifest["blob"] = f"snapshots/{name}.sqlite"
            upload(args.account, "backups", manifest["blob"], content, token,
                   "application/vnd.sqlite3")
            upload(args.account, "backups", "latest.json",
                   json.dumps(manifest).encode(), token, "application/json")
            prune_backups(args.account, token)
        else:
            if not args.excluded.is_file():
                raise RuntimeError("Validation-room exclusion configuration is missing")
            excluded = set(json.loads(args.excluded.read_text()))
            body, csv_data, summaries = render_report(copy, captured_at, excluded)
            manifest.update({"prefix": name, "rooms": summaries,
                             "results_sha256": hashlib.sha256(
                                 json.dumps(summaries, sort_keys=True).encode()).hexdigest(),
                             "csv_sha256": hashlib.sha256(csv_data).hexdigest(),
                             "html_sha256": hashlib.sha256(body).hexdigest()})
            if args.local_output:
                args.local_output.mkdir(parents=True, exist_ok=True)
                (args.local_output / "report.html").write_bytes(body)
                (args.local_output / "report.csv").write_bytes(csv_data)
                (args.local_output / "manifest.json").write_text(json.dumps(manifest))
            else:
                token = storage_token()
                for suffix, data, kind in (
                        ("report.html", body, "text/html"),
                        ("report.csv", csv_data, "text/csv"),
                        ("manifest.json", json.dumps(manifest).encode(), "application/json")):
                    upload(args.account, "reports", f"{name}/{suffix}", data, token, kind)
        print(json.dumps({key: value for key, value in manifest.items() if key != "rooms"}), flush=True)


if __name__ == "__main__":
    main()

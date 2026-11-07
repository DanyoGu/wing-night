#!/usr/bin/env python3
"""Install and read back externally hosted, bounded event schedules."""
from datetime import datetime, timedelta, timezone
import argparse
import json
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

SUBSCRIPTION = "26e7b9c0-b9a9-4745-9150-2a87fdb9749e"
BASE = (f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/resourceGroups/"
        "rg-hotones-lifecycle/providers/Microsoft.Automation/automationAccounts/aa-hotones")


def cli(*args):
    return json.loads(subprocess.check_output(["az.cmd", *args, "-o", "json"], text=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--enable-cleanup", action="store_true")
    parser.add_argument("--restore-evidence", type=Path)
    args = parser.parse_args()
    if cli("account", "show")["id"] != SUBSCRIPTION:
        raise RuntimeError("Unexpected Azure subscription")
    if args.enable_cleanup:
        if not args.restore_evidence:
            raise RuntimeError("Verified off-VM restore evidence is required")
        restore = json.loads(args.restore_evidence.read_text())
        if restore.get("off_vm_restore") != "ok" or restore.get("integrity") != "ok":
            raise RuntimeError("Off-VM restore was not verified")
        token = cli("account", "get-access-token", "--resource", "https://storage.azure.com/")["accessToken"]
        with urlopen(Request("https://sthotones20261106.blob.core.windows.net/reports/lifecycle.json",
                             headers={"Authorization": "Bearer " + token, "x-ms-version": "2023-11-03"}),
                     timeout=30) as response:
            state = json.load(response)
        if state.get("verified", {}).get("sender_auth") != "verified-no-send":
            raise RuntimeError("External reporting integration has not been verified")
    token = cli("account", "get-access-token", "--resource", "https://management.azure.com/")["accessToken"]

    def rest(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        with urlopen(Request(BASE + path + "?api-version=2023-11-01", data=data, method=method,
                             headers={"Authorization": "Bearer " + token,
                                      "Content-Type": "application/json"}), timeout=45) as response:
            content = response.read()
            return json.loads(content) if content else None

    definitions = [
        ("report-sunday", "Report", "2026-11-09T04:00:00Z", None),
        ("report-retry", "Report", "2026-11-09T05:00:00Z", "2026-11-09T12:30:00Z"),
        ("cleanup-monday", "Cleanup", "2026-11-09T13:00:00Z", None),
        ("cleanup-retry", "Cleanup", "2026-11-09T14:00:00Z", "2026-11-10T12:30:00Z"),
        ("retire-resources", "Retire", "2026-11-09T13:20:00Z", None),
        ("retire-retry", "Retire", "2026-11-09T14:20:00Z", "2026-11-10T12:50:00Z"),
    ]
    heartbeat = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=10)
    for i in range(4):
        definitions.append((f"backup-heartbeat-{i}", "Heartbeat",
                            (heartbeat + timedelta(minutes=15 * i)).isoformat(),
                            "2026-11-09T12:59:59Z"))
    evidence = []
    registrations = rest("GET", "/jobSchedules")["value"]
    for name, action, start, end in definitions:
        enabled = args.enable_cleanup or action not in ("Cleanup", "Retire")
        properties = {"startTime": start, "timeZone": "UTC", "isEnabled": enabled,
                      "description": f"Hotones {action}; externally scheduled with bounded retries",
                      "frequency": "Hour" if end else "OneTime"}
        if end:
            properties.update({"interval": 1, "expiryTime": end})
        try:
            existing = rest("GET", f"/schedules/{name}")
        except HTTPError as error:
            if error.code != 404:
                raise
            existing = None
        if existing:
            if not existing["properties"].get("description", "").startswith("Hotones "):
                raise RuntimeError(f"Refusing unrelated existing schedule {name}")
            rest("PATCH", f"/schedules/{name}", {"properties": {"isEnabled": enabled}})
        else:
            rest("PUT", f"/schedules/{name}", {"properties": properties})
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, BASE + name + action))
        matches = [item for item in registrations if
                   item["properties"]["schedule"]["name"] == name and
                   item["properties"]["runbook"]["name"] == "hotones-lifecycle"]
        if len(matches) > 1:
            raise RuntimeError(f"Multiple registrations exist for {name}")
        if matches:
            job_id = matches[0]["properties"]["jobScheduleId"]
        else:
            created = rest("PUT", f"/jobSchedules/{job_id}", {
                "properties": {"schedule": {"name": name}, "runbook": {"name": "hotones-lifecycle"},
                               "parameters": {"Action": action}}})
            job_id = created["properties"]["jobScheduleId"]
        readback = rest("GET", f"/schedules/{name}")["properties"]
        linked = rest("GET", f"/jobSchedules/{job_id}")["properties"]
        if (readback["isEnabled"] != enabled or readback["timeZone"] not in ("UTC", "Etc/UTC") or
                readback["startTimeOffsetMinutes"] != 0 or
                linked["parameters"]["Action"] != action or
                linked["runbook"]["name"] != "hotones-lifecycle"):
            raise RuntimeError(f"Schedule/link readback mismatch for {name}")
        if (not existing or action != "Heartbeat") and datetime.fromisoformat(readback["startTime"]) != datetime.fromisoformat(start.replace("Z", "+00:00")):
            raise RuntimeError(f"Schedule start readback mismatch for {name}")
        evidence.append({"name": name, "action": action, "start": readback["startTime"],
                         "expires": readback["expiryTime"], "frequency": readback["frequency"],
                         "zone": readback["timeZone"], "enabled": readback["isEnabled"]})
    args.output.write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()

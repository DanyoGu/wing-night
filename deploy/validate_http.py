#!/usr/bin/env python3
"""Disposable 18-session deployed HTTP test; never resets an existing room."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor


class Client:
    def __init__(self, base, token=None):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.jar))
        self.token = token

    def call(self, action, data=None, origin=None):
        headers = {}
        if self.token:
            headers["Cookie"] = "wing_session=" + self.token
        if data is not None:
            headers.update({"Content-Type": "application/json", "Origin": origin or self.base})
        path = "/api/" + action
        with self.opener.open(Request(self.base + path,
                                      data=None if data is None else json.dumps(data).encode(),
                                      headers=headers), timeout=20) as response:
            cookie = response.headers.get("Set-Cookie")
            if cookie:
                assert all(part in cookie for part in ("Secure", "HttpOnly", "SameSite=Lax"))
            return json.load(response)

    def saved_token(self):
        return self.token or next(cookie.value for cookie in self.jar if cookie.name == "wing_session")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://hotones.alvinli.work")
    parser.add_argument("--sessions", type=Path, default=Path("/root/wing-validation-sessions.json"))
    parser.add_argument("--excluded", type=Path, default=Path("/etc/wing-night/validation-rooms.json"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.resume:
        saved = json.loads(args.sessions.read_text())
        state = Client(args.url, saved["host"]).call("state?room=" + saved["room"])
        assert state["totals"] == saved["totals"] and state["phase"] == "finished"
        for token in saved["judges"]:
            mine = Client(args.url, token).call("state?room=" + saved["room"])
            assert mine["me"]["role"] == "judge" and len(mine["ownVotes"]) == 10
        print(json.dumps({"restart_persistence": "ok", "room": saved["room"], "totals": state["totals"]}))
        return
    host = Client(args.url)
    room = host.call("create", {})["room"]
    excluded = json.loads(args.excluded.read_text())
    excluded.append(room)
    args.excluded.write_text(json.dumps(excluded))
    clients = [Client(args.url) for _ in range(18)]
    for i, client in enumerate(clients):
        client.call("join", {"room": room, "name": f"AZURE-VALIDATION-{i:02d}"})
    anon = Client(args.url).call("state?room=" + room)
    assert anon["judgeCount"] == 18 and "judges" not in anon
    host.call("start", {"room": room})
    stop = threading.Event()
    poll_errors = []
    polls = [0] * 18

    def poll(i):
        try:
            while not stop.is_set():
                state = clients[i].call("state?room=" + room)
                assert state["judgeCount"] == 18
                assert state["totals"] == [sum(r["points"][p] for r in state["rounds"]) for p in range(2)]
                polls[i] += 1
                stop.wait(0.5)
        except Exception as error:
            poll_errors.append(repr(error))
            stop.set()

    def grades(i, round_index):
        return [(i + round_index + shift) % 5 + 1 for shift in range(4)]

    expected = [0, 0]
    threads = [threading.Thread(target=poll, args=(i,)) for i in range(18)]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=18) as pool:
            for round_index in range(10):
                def submit(i):
                    clients[i].call("vote", {"room": room, "round": round_index, "grades": [1, 1, 1, 1]})
                    final = grades(i, round_index)
                    clients[i].call("vote", {"room": room, "round": round_index, "grades": final})
                    mine = clients[i].call("state?room=" + room)
                    assert mine["ownVotes"][str(round_index)] == final
                list(pool.map(submit, range(18)))
                points = [sum(sum(grades(i, round_index)[p * 2:p * 2 + 2]) for i in range(18)) for p in range(2)]
                expected = [expected[p] + points[p] for p in range(2)]
                state = host.call("state?room=" + room)
                assert state["totals"] == expected
                assert state["rounds"][round_index]["submitted"] == 18
                assert state["rounds"][round_index]["points"] == points
                host.call("close", {"room": room})
                host.call("next", {"room": room})
                if round_index == 4:
                    # Keep live polling across at least one scheduled backup interval.
                    time.sleep(65)
        state = host.call("state?room=" + room)
        assert state["phase"] == "finished" and state["totals"] == [1080, 1080]
        try:
            host.call("create", {}, origin="https://evil.example")
            raise AssertionError("Cross-origin request accepted")
        except HTTPError as error:
            assert error.code == 403
        assert not poll_errors, poll_errors
        assert min(polls) > 100
        args.sessions.write_text(json.dumps({"room": room, "host": host.saved_token(),
                                            "judges": [c.saved_token() for c in clients], "totals": expected}))
        args.sessions.chmod(0o600)
        print(json.dumps({"http_concurrency": "ok", "judges": 18, "rounds": 10,
                          "initial_and_edit_requests": 360, "polls": sum(polls),
                          "elapsed_seconds": round(time.monotonic() - started, 1),
                          "totals": expected, "room": room, "excluded_from_report": True}))
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=25)


if __name__ == "__main__":
    main()

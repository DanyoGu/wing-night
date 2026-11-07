# Wing Night

Mobile matchup and shared voting for Daniel vs. Joicee. Python 3.9+; no third-party packages required.

## Testing mode

The default page opens the shared lobby. Add `?test=1` to open an isolated, one-judge matchup preview. Tap any wing to test voting. Preview votes are memory-only and never touch the shared database. Refresh to reset.

## Start locally

Stop the old `python3 -m http.server` preview with Control+C. Run:

```sh
cd /Users/danielgu/Code/wing-night
python3 server.py
```

Open http://localhost:8040/?lobby=1 and choose **Create event · Host as Daniel**. That browser holds the host session. Copy the room invite. Use a different browser or private window to join as a judge; tabs in the same browser share one identity. Before the start, visitors enter a display name and automatically join the judging roster. After the start, new visitors join as spectators. Daniel and Joicee are spectators when entering by their competitor names; the host cannot vote. Keep cookies to reconnect. Losing browser cookies loses the session; the current version has no recovery flow.

For phones on the same Wi-Fi, open the Mac's local network address at port 8040 (for example `http://192.168.1.25:8040`) on the host browser, then copy its invite. The Mac must stay awake, and its firewall must permit the server. A localhost invite opens the recipient's own device, so use the Mac's actual network address. This is a local rehearsal, not public hosting.

## Event flow

1. Host creates the room; judges join through its link or room code.
2. Projections and maximum points scale with actual registered judges, not RSVPs.
3. Host starts, locking the roster and opening round one.
4. Judges tap the active wing and submit four grades (1–5). Edits replace previous grades until closure.
5. Totals and projections update for everyone through polling every two seconds.
6. Host sees missing judges and can close only after every active judge has submitted. Open the next round or finish after round ten.

### Missing judges and spectators

- Disconnections never remove a judge or erase votes. Reconnect in the original browser.
- Before starting, the host can remove duplicate judges from the lobby with confirmation. Removed identities retain spectator access.
- During the match, **Manage judges → Withdraw** requires confirmation. The server atomically removes all of that judge’s grades from every round, withdraws their voting rights, and records the roster change. All totals, projections, maximums, and submission counts recalculate. The person may continue watching.
- At least one judge must remain; the final judge cannot be withdrawn. Finished results cannot be changed through withdrawal.
- Late arrivals are spectators. Neither spectators nor withdrawn/removed judges can vote or control rounds. Existing judges retain their original role when reconnecting.
- Roster changes are visible to participants. No force-close option bypasses missing votes.

Forecast per player per round = points already earned + per-judge estimate × judges not yet submitted. Top forecast is the sum of round forecasts. Win chances are illustrative estimates, not calibrated predictions. Maximum score is 100 × registered judges per player.

## Storage and permissions

SQLite is stored in `data/wing-night.sqlite`, excluded from Git. The server stores only hashes of opaque session tokens; cookies are HttpOnly and SameSite=Lax. Host permissions, grades, round lifecycle, roster locking, and duplicate submissions are enforced by the server. Name collisions require a distinguishing initial. A new browser can join with another name, so this is lightweight event access, not verified-person identity.

`PORT`, `WING_DB`, and `WING_SECURE_COOKIES=1` configure the server. CLI flags: `--host`, `--port`, `--db`, `--secure-cookies`.

## Public hosting

This backend needs one Python service with a **persistent disk**, plus HTTPS. It does not run on a static-only host or the current static Sites deployment. Mount the persistent disk at `/data`, set `WING_DB=/data/wing-night.sqlite` and `WING_SECURE_COOKIES=1`, and forward traffic to the service's `PORT` while preserving the original Host and Origin. Use a single service instance; SQLite is local to its disk. Back up the database before the event. The supplied Dockerfile packages this setup.

The temporary November 2026 Azure deployment is available at https://hotones.alvinli.work. See [deployment operations](deploy/README.md) for disk persistence, private backups, scheduled email reporting, and guarded Monday teardown.

Never commit the database or expose its directory through static file hosting. Only `dist/` is served to visitors. A host-only Reset event button clears the current room’s judges, votes, results, and roster history after typing RESET. Host identity and room code are retained; other rooms are unaffected. All judges must rejoin. Stale reset requests are rejected using the event generation. Host-authorized roster changes remove the affected judge’s grades; no other vote-deletion endpoint is exposed.

## Checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Checks cover host authorization, duplicate names, reconnection, invalid grades, roster/round locking, 18 concurrent judges, vote edits, projection sums, persistence across a database reopen, HTTP origin/cookie behavior, and all ten rounds. Browser and multi-phone testing remain necessary before the live event. Rounds 2–3, 5–6, and 8–9 display their supplied question banks; rounds 1, 4, 7, and 10 use audience questions. The final winner celebration uses turbulent fire, rising embers, winner photos, scores, and a replay button. It appears once per room/reset in each browser tab after the host finishes the event; reduced-motion users get a still fire background. Try `?test=1` and choose Preview win animation. A round-by-round animated recap is not implemented.

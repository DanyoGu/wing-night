# Temporary Azure deployment

The November 2026 deployment serves https://hotones.alvinli.work from source revision
`852fd6ed201217e1474253527322600daf761a7e`. This is a fresh event database; the host
creates the real event in their own browser. Keep that browser's cookies.

## Resources and traffic

All resources belong to the enabled Visual Studio Enterprise Subscription,
`26e7b9c0-b9a9-4745-9150-2a87fdb9749e`, and were created specifically for this event.
Do not add unrelated resources to these groups.

| Resource group | Owned resources |
| --- | --- |
| `rg-hotones-event` | East US Ubuntu 24.04 `vm-hotones`, non-Spot `Standard_D2as_v5`; 32 GiB Standard SSD OS disk; 32 GiB Premium SSD data disk; dedicated VNet, subnet, NSG, NIC and static Standard IPv4 |
| `rg-hotones-backups` | Private GPv2 Standard LRS account `sthotones20261106`; private `backups` and `reports` containers; email delivery-status queue |
| `rg-hotones-lifecycle` | Azure Automation `aa-hotones`, ACS `acs-hotones`, managed email domain, identity-enabled Event Grid system topic/subscription, failure action group and metric alert |

Cloudflare record: DNS-only A, `hotones`, `20.119.50.218`, TTL Auto. The user owns
DNS; this deployment never changes Cloudflare. Caddy terminates publicly trusted
HTTPS on 443, redirects HTTP on 80, and preserves Host/Origin to localhost 8040.
SSH is restricted to the administrator's provisioning IP; 8040 and SQLite are
not exposed. Caddy and Docker are supervised by systemd.

The dedicated ext4 disk mounts by UUID at `/srv/wing-night/data`, bind-mounted as
`/data` in the UID-10001 container. `wing-night.service` requires the mount and
checks both mountpoint and the expected UUID before starting. A missing mount
cannot silently create an OS-disk replacement database.

## Backups and reporting

`wing-backup.timer` runs every minute. `operations.py` uses SQLite's online backup
API, checks SQLite and foreign-key integrity, uploads the snapshot and SHA-256
manifest, and updates a heartbeat only after upload succeeds. VM managed identity
has blob access only to the owned containers. No account keys, SAS, Gmail password,
or other credentials are embedded.

Backups are pruned to approximately 24 hours while healthy backups continue;
temporary files and journald/container logs are bounded. Four staggered hourly
Automation schedules check the heartbeat approximately every 15 minutes, with
up to a roughly 17-minute gap plus Azure job startup latency. A snapshot older
than five minutes fails the check. Failed/suspended/stopped Automation jobs
trigger a real Azure Monitor email alert; no test notification is sent.

Reports contain human-readable HTML and a UTF-8 CSV attachment: room, actual
phase, round, recorded totals, submission counts and individual grades for
**the answer** and **handling the heat**. Forecasts are not presented as scores.
Session cookies, token hashes and member IDs are excluded. Validation room codes
are recorded in the root-owned exclusion file; missing configuration aborts export.
Snapshot completion time is explicit. Export does not freeze voting.

Sender: `DoNotReply@b364dbde-247f-4d8c-b92d-e50898d2f8d8.azurecomm.net`.
Recipient: `alvinli222@gmail.com`, with no CC/BCC. Engagement tracking is disabled.
Sender authorization is verified using an invalid, recipient-free request;
no immediate test email is sent. The first real scheduled message still must
pass provider send and delivery checks.

## External schedules and safeguards

| Action | Requested EST time | Azure UTC start |
| --- | --- | --- |
| Sunday snapshot/report | November 8, 2026, 11:00 PM EST | November 9, 04:00 UTC |
| Stop writes, fresh export, guarded event deletion | November 9, 2026, 8:00 AM EST | November 9, 13:00 UTC |
| Remove backups and lifecycle resources after event deletion | After verified event cleanup | November 9, 13:20 UTC |

Azure job dispatch and resource deletion take time; these are schedule start
times, not guarantees of instant completion. Report retries are hourly until
Monday cleanup. Cleanup/retirement retries expire Tuesday morning, November 10.
The scheduler lives outside the VM group and does not depend on a laptop or a
VM deleting itself.

Cleanup requires verified export and an ACS **Delivered** event for the Sunday
message, not just HTTP 202 or a successful send operation. Delivered means the
recipient mail server accepted the message, not that it reached the inbox or
was read. Bounces, suppression, spam filtering and missing delivery evidence
block destruction.

At Monday shutdown the app is stopped before a fresh snapshot. If results changed
after Sunday's report, a final CSV/HTML report must also obtain successful send
and recipient-mail-server acceptance before deletion. Otherwise the Sunday report
is reused. Exact owned names/types and ownership tags are checked before deleting
only the event group. An independent retirement step removes operational backups
and its own lifecycle group after event deletion is confirmed.

On export/email failure, cleanup requests VM **deallocation**, preserves disks
and backups, fails visibly and alerts. Storage, disks, static IP and monitoring
can still incur small charges until recovery; a deallocated VM cannot execute
recovery commands until an administrator starts it. No emergency fallback deletes
the sole copy. Do not manually delete the backup group to suppress an alert.

Blob/container soft delete and blob versioning are disabled: successful teardown
retains no event archive. Azure may retain platform activity metadata; zero-cost
custom RBAC definitions and the user's DNS record can remain, but they contain
no judging results and do not keep the deleted resource groups running.

## Configuration and checks

`cloud-init.yml` prepares Docker/Caddy. `bootstrap.sh` prepares the approved blank
data disk and installs mount-guarded services. It must never be pointed at another
disk or used to reset a real event. ARM JSON files describe the owned sender,
queue delivery, identities, runbook and alerts. `lifecycle.ps1` is the published
external runbook; `create_schedules.py` creates/readbacks schedule links and uses
the canonical registration IDs returned by Azure.

Run deployment scripts only with the correct authorized subscription. Creating
schedules initially leaves cleanup disabled unless `--enable-cleanup` is supplied
with successful off-VM restore evidence and verified external sender integration.
Reapplying schedule links does not create duplicate registrations.

On Linux:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
```

On Windows, the approved process-only test execution is:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\tests\test_lifecycle.ps1
```

`validate_http.py` creates a disposable, excluded room with 18 independent secure
cookie sessions, exercises submissions/edits across all ten rounds with polling
and backups active, and saves private test sessions for restart checks. It never
resets a real room. `restore_verify.py` downloads actual off-VM snapshots, verifies
checksums/integrity, reopens restored SQLite and checks exact validation scores.
Never publish its downloaded SQLite data or private session/SSH key files.

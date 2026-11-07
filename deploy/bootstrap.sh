#!/bin/bash
set -euo pipefail
account="${1:?Storage account required}"
source_sha="${2:?Pinned source revision required}"
disk=/dev/disk/azure/scsi1/lun0
test -b "$disk"
if ! blkid "$disk" >/dev/null; then
    mkfs.ext4 -L wing-night-data "$disk"
fi
test "$(blkid -s TYPE -o value "$disk")" = ext4
uuid="$(blkid -s UUID -o value "$disk")"
mkdir -p /srv/wing-night/data /etc/wing-night
if ! grep -q "UUID=$uuid " /etc/fstab; then
    printf 'UUID=%s /srv/wing-night/data ext4 defaults,nofail 0 2\n' "$uuid" >> /etc/fstab
fi
systemctl daemon-reload
if ! mountpoint -q /srv/wing-night/data; then
    mount /srv/wing-night/data
fi
test "$(findmnt -rn -o UUID --target /srv/wing-night/data)" = "$uuid"
chown 10001:10001 /srv/wing-night/data
chmod 750 /srv/wing-night/data
printf 'WING_STORAGE_ACCOUNT=%s\n' "$account" > /etc/wing-night/environment
chmod 640 /etc/wing-night/environment
if [ ! -f /etc/wing-night/validation-rooms.json ]; then
    printf '[]\n' > /etc/wing-night/validation-rooms.json
fi
if ! docker image inspect "wing-night:$source_sha" >/dev/null 2>&1; then
    docker build --pull --quiet -t "wing-night:$source_sha" /opt/wing-night
fi
cat > /etc/systemd/system/wing-night.service <<EOF
[Unit]
Description=Wing Night single SQLite application
Requires=docker.service
After=docker.service network-online.target
RequiresMountsFor=/srv/wing-night/data
[Service]
ExecStartPre=/usr/bin/mountpoint -q /srv/wing-night/data
ExecStartPre=/bin/sh -c 'test "\$(findmnt -rn -o UUID --target /srv/wing-night/data)" = "$uuid"'
ExecStartPre=-/usr/bin/docker rm -f wing-night
ExecStart=/usr/bin/docker run --name wing-night --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 -p 127.0.0.1:8040:8040 -v /srv/wing-night/data:/data -e WING_SECURE_COOKIES=1 wing-night:$source_sha
ExecStop=/usr/bin/docker stop -t 20 wing-night
Restart=always
RestartSec=5
TimeoutStopSec=30
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/wing-backup.service <<EOF
[Unit]
Description=Integrity-checked off-VM SQLite snapshot
RequiresMountsFor=/srv/wing-night/data
After=wing-night.service network-online.target
[Service]
Type=oneshot
EnvironmentFile=/etc/wing-night/environment
RuntimeDirectory=wing-backup
RuntimeDirectoryMode=0700
Environment=TMPDIR=/run/wing-backup
ExecStart=/usr/bin/python3 /opt/wing-night/deploy/operations.py backup
TimeoutStartSec=55
EOF
cat > /etc/systemd/system/wing-backup.timer <<'EOF'
[Unit]
Description=Minute-by-minute Wing Night backups
[Timer]
OnBootSec=90s
OnUnitActiveSec=60s
AccuracySec=1s
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now wing-night.service
systemctl enable --now wing-backup.timer
for attempt in {1..20}; do
    if curl -fsS http://127.0.0.1:8040/ >/dev/null; then
        systemctl start wing-backup.service
        systemctl is-active wing-night caddy docker
        exit 0
    fi
    sleep 2
done
echo "Application did not become ready" >&2
exit 1

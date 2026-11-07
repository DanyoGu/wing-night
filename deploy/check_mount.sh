#!/bin/bash
set -euo pipefail
if [ "${1:-}" != namespace ]; then
    exec unshare --mount --propagation private bash "$0" namespace
fi
umount /srv/wing-night/data
if mountpoint -q /srv/wing-night/data; then
    echo "Missing-mount guard unexpectedly accepted an unmounted directory" >&2
    exit 1
fi
echo "Missing-mount guard passed in isolated mount namespace"

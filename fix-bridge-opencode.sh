#!/bin/bash
set -euo pipefail

TARGET="/home/ubuntu/clawith-bridge/bridge.py"

cp -p "$TARGET" "$TARGET.bak"

# Allow full URL override while keeping host/port fallback.
sed -i 's#^OPENCODE_URL = .*#OPENCODE_URL = os.environ.get("OPENCODE_URL", f"http://{OPENCODE_HOST}:{OPENCODE_PORT}")#' "$TARGET"

echo "=== Updated snippet ==="
sed -n '36,46p' "$TARGET"

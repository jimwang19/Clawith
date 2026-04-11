#!/bin/bash
# 在 ubu24.04-opcdev 中启动 opencode serve

set -e

DISTRO="ubu24.04-opcdev"
PORT="4096"

echo "=========================================="
echo "Starting opencode serve on $DISTRO:$PORT"
echo "=========================================="

# 先检查是否已在运行
echo ""
echo "=== 1. Check if opencode serve is already running ==="
if wsl -d "$DISTRO" -- pgrep -af "opencode serve" > /dev/null 2>&1; then
    echo "⚠ opencode serve is already running"
    echo "Stopping previous instance..."
    wsl -d "$DISTRO" -- pkill -f "opencode serve" || true
    sleep 2
fi

echo ""
echo "=== 2. Verify opencode is available ==="
wsl -d "$DISTRO" -- bash -c "
  if command -v opencode > /dev/null; then
    echo '✓ opencode found:'; command -v opencode;
    echo 'Version:'; opencode --version 2>/dev/null || true;
  else
    echo 'ERROR: opencode not found';
    exit 1;
  fi
"

echo ""
echo "=== 3. Check if port $PORT is available ==="
wsl -d "$DISTRO" -- bash -c "
  python3 - <<'PY'
import socket
import sys
s = socket.socket()
try:
    s.bind(('0.0.0.0', $PORT))
    print('✓ Port $PORT is available')
    s.close()
except OSError as e:
    print('✗ Port $PORT is NOT available:', e.strerror)
    s.close()
    sys.exit(1)
PY
" || {
    echo "ERROR: Port $PORT is in use"
    exit 1
}

echo ""
echo "=== 4. Create opencode serve startup script ==="
wsl -d "$DISTRO" -- bash -c "
  cat > ~/.opencode-serve-start.sh << 'START_SCRIPT'
#!/bin/bash
# OpenCode serve startup script for clawith-bridge

export PATH=/usr/local/bin:/usr/bin:/bin:\$PATH

echo 'Starting opencode serve...'
echo \"Time: \$(date '+%Y-%m-%d %H:%M:%S')\"
echo \"User: \$(whoami)\"
echo \"Working directory: \$(pwd)\"
echo \"PID: \$\$\"

# Create logs directory if it doesn't exist
mkdir -p ~/.opencode/logs 2>/dev/null || true

# Start opencode serve with output to log
exec opencode serve --port $PORT --hostname 0.0.0.0 \
  >> ~/.opencode/logs/opencode-serve.log 2>&1
START_SCRIPT

  chmod +x ~/.opencode-serve-start.sh
  echo '✓ Startup script created at ~/.opencode-serve-start.sh'
"

echo ""
echo "=== 5. Start opencode serve (background) ==="
wsl -d "$DISTRO" -- bash -c "
  nohup ~/.opencode-serve-start.sh > /tmp/opencode-serve-nohup.log 2>&1 &
  sleep 2
  
  if pgrep -af 'opencode serve' > /dev/null; then
    echo '✓ opencode serve started successfully'
    ps auxww | grep -E 'opencode serve' | grep -v grep || true
  else
    echo 'ERROR: opencode serve failed to start'
    cat /tmp/opencode-serve-nohup.log
    exit 1
  fi
"

echo ""
echo "=== 6. Verify opencode API is responding ==="
sleep 3
curl -s http://127.0.0.1:$PORT/global/health >/dev/null 2>&1 && \
  echo '✓ OpenCode API is responding at http://127.0.0.1:'$PORT \
  || echo '⚠ API not responding yet, may still be starting up'

echo ""
echo "=========================================="
echo "✓ Setup complete!"
echo "=========================================="
echo ""
echo "OpenCode serve is running on:"
echo "  Distribution: $DISTRO"
echo "  Address: http://127.0.0.1:$PORT"
echo "  Logs: /home/ubuntu/.opencode/logs/opencode-serve.log"
echo ""
echo "Bridge will automatically connect to this instance."
echo ""

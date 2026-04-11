#!/bin/bash
# Start OpenCode serve on port 4096 in ubu24.04-opcdev
echo '[opencode] Stopping any existing process...'
pkill -f 'opencode serve' 2>/dev/null || true
sleep 1

echo '[opencode] Starting opencode serve on 0.0.0.0:4096...'
nohup opencode serve --port 4096 --hostname 0.0.0.0 >> /tmp/opencode-serve.log 2>&1 &
sleep 3

if pgrep -f 'opencode serve' > /dev/null; then
  echo '[opencode] ✓ Started successfully'
  ps auxww | grep 'opencode serve' | grep -v grep | head -1
else
  echo '[opencode] ✗ Failed to start'
  tail -20 /tmp/opencode-serve.log
fi

#!/bin/bash
set -euo pipefail

cd /home/ubuntu/clawith-bridge
mkdir -p logs

pkill -f '/home/ubuntu/clawith-bridge/bridge.py' 2>/dev/null || true
sleep 1

set -a
source ./config.env
set +a

nohup python3 bridge.py >> logs/bridge.log 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > logs/bridge.pid

echo "bridge restarted with env pid=$NEW_PID"
ps -ef | grep -E '/home/ubuntu/clawith-bridge/bridge.py|opencode serve --port 4096' | grep -v grep

echo "--- bridge tail ---"
tail -n 12 logs/bridge.log

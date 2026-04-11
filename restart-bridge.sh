#!/bin/bash
set -euo pipefail

cd /home/ubuntu/clawith-bridge
mkdir -p logs

pkill -f '/home/ubuntu/clawith-bridge/bridge.py' 2>/dev/null || true
sleep 1

nohup python3 bridge.py >> logs/bridge.log 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > logs/bridge.pid

echo "bridge restarted pid=$NEW_PID"
ps -ef | grep -E 'opencode serve|/home/ubuntu/clawith-bridge/bridge.py' | grep -v grep

echo "--- bridge log tail ---"
tail -n 25 logs/bridge.log

#!/bin/bash
pkill -f hermes-openclaw-bridge || true
sleep 1
set -a
source /home/ubuntu/clawith-bridge/config.env
set +a
nohup python3 /home/ubuntu/clawith-bridge/hermes-openclaw-bridge.py \
    >> /home/ubuntu/clawith-bridge/logs/hermes-openclaw.log 2>&1 &
echo $! > /home/ubuntu/clawith-bridge/logs/hermes-openclaw.pid
sleep 2
echo "PID=$(cat /home/ubuntu/clawith-bridge/logs/hermes-openclaw.pid)"
tail -4 /home/ubuntu/clawith-bridge/logs/hermes-openclaw.log

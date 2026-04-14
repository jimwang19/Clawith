#!/bin/bash
set -a
source /home/ubuntu/clawith-bridge/config.env
set +a
mkdir -p /home/ubuntu/clawith-bridge/logs
nohup python3 /home/ubuntu/clawith-bridge/hermes-openclaw-bridge.py \
    >> /home/ubuntu/clawith-bridge/logs/hermes-openclaw.log 2>&1 &
echo $! > /home/ubuntu/clawith-bridge/logs/hermes-openclaw.pid
echo "Started PID=$(cat /home/ubuntu/clawith-bridge/logs/hermes-openclaw.pid)"
sleep 3
tail -5 /home/ubuntu/clawith-bridge/logs/hermes-openclaw.log

#!/bin/bash
# /home/ubuntu/clawith-bridge/run-forever.sh
# 持久运行脚本：opencode 和 bridge 都在这里启动，如果崩溃自动重启
# 通过 crontab @reboot 调用此脚本

BRIDGE_DIR="/home/ubuntu/clawith-bridge"
LOG_DIR="$BRIDGE_DIR/logs"
OPENCODE_BIN="/usr/local/bin/opencode"
OPENCODE_WORKDIR="/code"
OPENCODE_PORT=4096

mkdir -p "$LOG_DIR"
set -a
source "$BRIDGE_DIR/config.env"
set +a

echo "[$(date '+%Y-%m-%d %H:%M:%S')] run-forever started (pid=$$)" >> "$LOG_DIR/run-forever.log"

# 启动 opencode serve（如果没在跑）
start_opencode() {
    if ! pgrep -f "opencode serve" > /dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting opencode serve..." >> "$LOG_DIR/run-forever.log"
        cd "$OPENCODE_WORKDIR"
        nohup "$OPENCODE_BIN" serve --port "$OPENCODE_PORT" --hostname "0.0.0.0" \
            >> "$LOG_DIR/opencode.log" 2>&1 &
        echo $! > "$LOG_DIR/opencode.pid"
        sleep 5
    fi
}

# 以 setsid 方式启动 bridge.py，确保 watchdog 自己不会被阻塞
start_bridge() {
    if ! pgrep -f "python3 $BRIDGE_DIR/bridge.py" > /dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting bridge.py with setsid..." >> "$LOG_DIR/run-forever.log"
        setsid python3 "$BRIDGE_DIR/bridge.py" >> "$LOG_DIR/bridge.log" 2>&1 &
        echo $! > "$LOG_DIR/bridge.pid"
        sleep 2
    fi
}

# 重启 bridge（让它重连新的 opencode）
restart_bridge() {
    pkill -f "python3 $BRIDGE_DIR/bridge.py" 2>/dev/null || true
    rm -f "$LOG_DIR/bridge.pid"
    sleep 2
    start_bridge
}

# 主循环
start_opencode
start_bridge

OPENCODE_CHECK=0
while true; do
    # 每 30 秒检查一次 opencode 健康（15 * 2s = 30s）
    OPENCODE_CHECK=$((OPENCODE_CHECK + 1))
    if [ $OPENCODE_CHECK -ge 15 ]; then
        OPENCODE_CHECK=0
        if ! pgrep -f "opencode serve" > /dev/null; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] opencode not running, restarting..." >> "$LOG_DIR/run-forever.log"
            start_opencode
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restarting bridge to reconnect to opencode..." >> "$LOG_DIR/run-forever.log"
            restart_bridge
        fi
    fi

    if ! pgrep -f "python3 $BRIDGE_DIR/bridge.py" > /dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] bridge.py not running, restarting..." >> "$LOG_DIR/run-forever.log"
        start_opencode
        start_bridge
    fi
    sleep 2
done

#!/bin/bash
# /home/ubuntu/clawith-bridge/run-forever.sh
# 持久运行脚本：opencode serve、bridge.py、hermes-mcp-bridge 都在这里启动
# 崩溃后自动重启，通过 crontab @reboot 调用

BRIDGE_DIR="/home/ubuntu/clawith-bridge"
LOG_DIR="$BRIDGE_DIR/logs"
OPENCODE_BIN="/usr/local/bin/opencode"
OPENCODE_WORKDIR="/code"
OPENCODE_PORT=4096

# Hermes MCP bridge
HERMES_BRIDGE_SCRIPT="$BRIDGE_DIR/../hermes-mcp-bridge.py"
HERMES_OPENCLAW_SCRIPT="$BRIDGE_DIR/hermes-openclaw-bridge.py"
HERMES_BRIDGE_PORT=8888
HERMES_REMOTE_PORT=9999
SSH_TUNNEL_TARGET="ubuntu@tencent-wyh"

mkdir -p "$LOG_DIR"
set -a
source "$BRIDGE_DIR/config.env"
set +a

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/run-forever.log"
}

log "run-forever started (pid=$$)"

# ── opencode serve ─────────────────────────────────────────────────────────

is_opencode_running() {
    local pid_file="$LOG_DIR/opencode.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    curl -sf --connect-timeout 2 "http://127.0.0.1:${OPENCODE_PORT}/global/health" > /dev/null 2>&1
}

start_opencode() {
    if ! is_opencode_running; then
        log "Starting opencode serve..."
        cd "$OPENCODE_WORKDIR" || cd /
        setsid "$OPENCODE_BIN" serve --port "$OPENCODE_PORT" --hostname "0.0.0.0" \
            >> "$LOG_DIR/opencode.log" 2>&1 &
        echo $! > "$LOG_DIR/opencode.pid"
        local i=0
        while [ $i -lt 15 ]; do
            sleep 2
            if curl -sf --connect-timeout 2 "http://127.0.0.1:${OPENCODE_PORT}/global/health" > /dev/null 2>&1; then
                log "opencode serve is healthy"
                return 0
            fi
            i=$((i + 1))
        done
        log "WARNING: opencode serve did not become healthy in time"
    fi
}

# ── bridge.py ──────────────────────────────────────────────────────────────

is_bridge_running() {
    local pid_file="$LOG_DIR/bridge.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        kill -0 "$pid" 2>/dev/null
    else
        return 1
    fi
}

start_bridge() {
    if ! is_bridge_running; then
        log "Starting bridge.py..."
        setsid python3 "$BRIDGE_DIR/bridge.py" >> "$LOG_DIR/bridge.log" 2>&1 &
        echo $! > "$LOG_DIR/bridge.pid"
        sleep 2
    fi
}

restart_bridge() {
    local pid_file="$LOG_DIR/bridge.pid"
    if [ -f "$pid_file" ]; then
        kill "$(cat "$pid_file")" 2>/dev/null || true
        rm -f "$pid_file"
    fi
    sleep 2
    start_bridge
}
# ── Hermes OpenClaw Bridge ────────────────────────────────────────────────

is_hermes_openclaw_running() {
    local pid_file="$LOG_DIR/hermes-openclaw.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        kill -0 "$pid" 2>/dev/null
    else
        return 1
    fi
}

start_hermes_openclaw() {
    if ! is_hermes_openclaw_running; then
        if [ -z "$HERMES_API_KEY" ]; then
            log "HERMES_API_KEY not set, skipping hermes-openclaw-bridge"
            return 0
        fi
        log "Starting hermes-openclaw-bridge..."
        setsid python3 "$HERMES_OPENCLAW_SCRIPT" >> "$LOG_DIR/hermes-openclaw.log" 2>&1 &
        echo $! > "$LOG_DIR/hermes-openclaw.pid"
        sleep 2
        if is_hermes_openclaw_running; then
            log "hermes-openclaw-bridge started (pid=$(cat $LOG_DIR/hermes-openclaw.pid))"
        else
            log "WARNING: hermes-openclaw-bridge failed to start"
        fi
    fi
}

# ── Hermes MCP Bridge ─────────────────────────────────────────────────────

is_hermes_bridge_running() {
    local pid_file="$LOG_DIR/hermes-bridge.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        kill -0 "$pid" 2>/dev/null
    else
        return 1
    fi
}

start_hermes_bridge() {
    if ! is_hermes_bridge_running; then
        log "Starting hermes-mcp-bridge on port $HERMES_BRIDGE_PORT..."
        setsid python3 "$HERMES_BRIDGE_SCRIPT" --port "$HERMES_BRIDGE_PORT" --host 0.0.0.0 \
            >> "$LOG_DIR/hermes-mcp-bridge.log" 2>&1 &
        echo $! > "$LOG_DIR/hermes-bridge.pid"
        sleep 3
        # Verify it's running
        if curl -sf --connect-timeout 3 "http://127.0.0.1:${HERMES_BRIDGE_PORT}/health" > /dev/null 2>&1; then
            log "hermes-mcp-bridge is healthy"
        else
            log "WARNING: hermes-mcp-bridge did not become healthy"
        fi
    fi
}

stop_hermes_bridge() {
    local pid_file="$LOG_DIR/hermes-bridge.pid"
    if [ -f "$pid_file" ]; then
        kill "$(cat "$pid_file")" 2>/dev/null || true
        rm -f "$pid_file"
    fi
    pkill -f "hermes-mcp-bridge.py" 2>/dev/null || true
}

# ── SSH 反向隧道 ──────────────────────────────────────────────────────────

is_tunnel_running() {
    # Check if SSH tunnel process exists
    pgrep -f "ssh.*-R.*${HERMES_REMOTE_PORT}.*${SSH_TUNNEL_TARGET}" > /dev/null 2>&1
}

start_tunnel() {
    if is_tunnel_running; then
        log "SSH tunnel already running, skipping"
        return 0
    fi
    log "Starting SSH reverse tunnel -> ${HERMES_REMOTE_PORT} on tencent-wyh..."
    # Kill stale tunnels
    pkill -f "ssh.*-R.*${HERMES_REMOTE_PORT}" 2>/dev/null || true
    sleep 1
    ssh -f -N -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 0.0.0.0:${HERMES_REMOTE_PORT}:localhost:${HERMES_BRIDGE_PORT} \
        "$SSH_TUNNEL_TARGET" >> "$LOG_DIR/run-forever.log" 2>&1
    sleep 2
    # Verify tunnel is up
    if ssh -o ConnectTimeout=5 "$SSH_TUNNEL_TARGET" "curl -sf --connect-timeout 3 http://127.0.0.1:${HERMES_REMOTE_PORT}/health" > /dev/null 2>&1; then
        log "SSH tunnel verified working"
    else
        log "WARNING: SSH tunnel may not be working"
    fi
}

stop_tunnel() {
    pkill -f "ssh.*-R.*${HERMES_REMOTE_PORT}" 2>/dev/null || true
}

# ── 首次启动 ──────────────────────────────────────────────────────────────

start_opencode
start_bridge
start_hermes_bridge
start_hermes_openclaw
start_tunnel

# ── 看门狗主循环 ──────────────────────────────────────────────────────────

OPENCODE_CHECK=0
TUNNEL_CHECK=0

log "Watchdog loop started"
while true; do
    sleep 2
    OPENCODE_CHECK=$((OPENCODE_CHECK + 1))
    TUNNEL_CHECK=$((TUNNEL_CHECK + 1))

    # 每 30 秒检查 opencode
    if [ $OPENCODE_CHECK -ge 15 ]; then
        OPENCODE_CHECK=0
        if ! is_opencode_running; then
            log "opencode not running, restarting..."
            start_opencode
            log "Restarting bridge to reconnect to opencode..."
            restart_bridge
        fi
    fi

    # bridge.py
    if ! is_bridge_running; then
        log "bridge.py not running, restarting..."
        start_opencode
        start_bridge
    fi

    # hermes-openclaw-bridge
    if ! is_hermes_openclaw_running; then
        log "hermes-openclaw-bridge not running, restarting..."
        start_hermes_openclaw
    fi

    # hermes-mcp-bridge (每 30 秒检查)
    if [ $((TUNNEL_CHECK % 15)) -eq 0 ]; then
        if ! is_hermes_bridge_running; then
            log "hermes-mcp-bridge not running, restarting..."
            start_hermes_bridge
        fi
        # SSH 隧道 (每 60 秒检查)
        if [ $((TUNNEL_CHECK % 30)) -eq 0 ]; then
            if ! is_tunnel_running; then
                log "SSH tunnel down, restarting..."
                start_tunnel
            fi
        fi
    fi
done
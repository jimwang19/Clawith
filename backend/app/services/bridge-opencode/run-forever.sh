#!/bin/bash
BRIDGE_DIR="/home/ubuntu/clawith-bridge-opencode"
LOG_DIR="$BRIDGE_DIR/logs"
OPENCODE_BIN="/usr/local/bin/opencode"
OPENCODE_WORKDIR="/code"
OPENCODE_PORT=4096

mkdir -p "$LOG_DIR"
set -a
source "$BRIDGE_DIR/config.env"
set +a

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/run-forever.log"
}

log "run-forever-opencode started (pid=$$)"

is_opencode_running() {
    local pid_file="$LOG_DIR/opencode.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            curl -sf --connect-timeout 2 "http://127.0.0.1:${OPENCODE_PORT}/global/health" > /dev/null 2>&1
            return $?
        else
            rm -f "$pid_file"
            return 1
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

is_bridge_running() {
    local pid_file="$LOG_DIR/bridge.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        else
            rm -f "$pid_file"
            return 1
        fi
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

rotate_logs() {
    local max_size=10485760
    for log_file in "$LOG_DIR"/*.log; do
        if [[ -f "$log_file" ]] && [[ $(stat -c%s "$log_file" 2>/dev/null || echo 0) -gt $max_size ]]; then
            local date_suffix=$(date +%Y%m%d_%H%M%S)
            mv "$log_file" "${log_file}.${date_suffix}.bkp"
            (
                cd "$(dirname "$log_file")" || return 0
                ls -t "$(basename "$log_file")."*.bkp 2>/dev/null | tail -n +11 | xargs -r rm -f
            )
        fi
    done
}

start_opencode
start_bridge

log "Watchdog loop started"
OPENCODE_CHECK=0
LOG_ROTATION_COUNTER=0

while true; do
    sleep 2
    OPENCODE_CHECK=$((OPENCODE_CHECK + 1))
    LOG_ROTATION_COUNTER=$((LOG_ROTATION_COUNTER + 1))

    if [ $((LOG_ROTATION_COUNTER % 30)) -eq 0 ]; then
        rotate_logs
    fi

    if [ $OPENCODE_CHECK -ge 15 ]; then
        OPENCODE_CHECK=0
        if ! is_opencode_running; then
            log "opencode not running, restarting..."
            start_opencode
            log "Restarting bridge to reconnect to opencode..."
            restart_bridge
        fi
    fi

    if ! is_bridge_running; then
        log "bridge.py not running, restarting..."
        start_opencode
        start_bridge
    fi
done

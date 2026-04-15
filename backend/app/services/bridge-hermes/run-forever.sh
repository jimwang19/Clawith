#!/bin/bash
BRIDGE_DIR="/home/ubuntu/clawith-bridge-hermes"
LOG_DIR="$BRIDGE_DIR/logs"
HERMES_OPENCLAW_SCRIPT="$BRIDGE_DIR/hermes-openclaw-bridge.py"
HERMES_BRIDGE_SCRIPT="$BRIDGE_DIR/hermes-mcp-bridge.py"

mkdir -p "$LOG_DIR"
set -a
source "$BRIDGE_DIR/config.env"
set +a

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/run-forever.log"
}

log "run-forever-hermes started (pid=$$)"

port_available_check() {
    local port=$1
    if command -v ss >/dev/null 2>&1; then
        ss -tlnp 2>/dev/null | grep -q ":$port "
    elif command -v netstat >/dev/null 2>&1; then
        netstat -tlnp | grep -q ":$port "
    else
        lsof -i :$port 2>/dev/null | grep -q LISTEN
    fi
    return $?
}

is_hermes_openclaw_running() {
    local pid_file="$LOG_DIR/hermes-openclaw.pid"
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
            OPENCLAW_FAIL_COUNT=0
        else
            OPENCLAW_FAIL_COUNT=$((OPENCLAW_FAIL_COUNT + 1))
            log "WARNING: hermes-openclaw-bridge failed to start (attempt $OPENCLAW_FAIL_COUNT)"
        fi
    fi
}

is_hermes_bridge_running() {
    local pid_file="$LOG_DIR/hermes-bridge.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            if curl -sf --connect-timeout 3 "http://127.0.0.1:${HERMES_BRIDGE_PORT}/health" > /dev/null 2>&1; then
                return 0
            else
                log "hermes-mcp-bridge: Process $pid exists but service not responding, restarting"
                return 1
            fi
        else
            rm -f "$pid_file"
            return 1
        fi
    else
        return 1
    fi
}

start_hermes_bridge() {
    if is_hermes_bridge_running; then
        log "hermes-mcp-bridge already running and healthy"
        return 0
    fi

    if port_available_check $HERMES_BRIDGE_PORT; then
        log "Port $HERMES_BRIDGE_PORT already in use, releasing it"
        pkill -f "hermes-mcp-bridge.py" 2>/dev/null || true
        fuser -k $HERMES_BRIDGE_PORT/tcp 2>/dev/null || true
        sleep 2
    fi

    log "Starting hermes-mcp-bridge on port $HERMES_BRIDGE_PORT..."
    setsid python3 "$HERMES_BRIDGE_SCRIPT" --port "$HERMES_BRIDGE_PORT" --host 0.0.0.0 \
        >> "$LOG_DIR/hermes-mcp-bridge.log" 2>&1 &
    echo $! > "$LOG_DIR/hermes-bridge.pid"
    sleep 3
    
    if curl -sf --connect-timeout 5 "http://127.0.0.1:${HERMES_BRIDGE_PORT}/health" > /dev/null 2>&1; then
        log "hermes-mcp-bridge is healthy"
    else
        log "WARNING: hermes-mcp-bridge did not become healthy"
        fuser -k $HERMES_BRIDGE_PORT/tcp 2>/dev/null || true
        sleep 2
        log "Retrying hermes-mcp-bridge startup after port cleanup"
        setsid python3 "$HERMES_BRIDGE_SCRIPT" --port "$HERMES_BRIDGE_PORT" --host 0.0.0.0 \
            >> "$LOG_DIR/hermes-mcp-bridge.log" 2>&1 &
        echo $! > "$LOG_DIR/hermes-bridge.pid"
        sleep 5
        if curl -sf --connect-timeout 5 "http://127.0.0.1:${HERMES_BRIDGE_PORT}/health" > /dev/null 2>&1; then
            log "hermes-mcp-bridge is now healthy"
        else
            log "ERROR: hermes-mcp-bridge failed to start"
        fi
    fi
}

is_tunnel_running() {
    pgrep -f "ssh.*-R.*${HERMES_REMOTE_PORT}.*${SSH_TUNNEL_TARGET}" > /dev/null 2>&1
}

start_tunnel() {
    if is_tunnel_running; then
        log "SSH tunnel already running, skipping"
        return 0
    fi
    log "Starting SSH reverse tunnel -> ${HERMES_REMOTE_PORT} on tencent-wyh..."
    pkill -f "ssh.*-R.*${HERMES_REMOTE_PORT}" 2>/dev/null || true
    sleep 1
    ssh -f -N -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 0.0.0.0:${HERMES_REMOTE_PORT}:localhost:${HERMES_BRIDGE_PORT} \
        "$SSH_TUNNEL_TARGET" >> "$LOG_DIR/run-forever.log" 2>&1
    sleep 2
    if ssh -o ConnectTimeout=5 "$SSH_TUNNEL_TARGET" "curl -sf --connect-timeout 3 http://127.0.0.1:${HERMES_REMOTE_PORT}/health" > /dev/null 2>&1; then
        log "SSH tunnel verified working"
    else
        log "WARNING: SSH tunnel may not be working"
    fi
}

stop_tunnel() {
    pkill -f "ssh.*-R.*${HERMES_REMOTE_PORT}" 2>/dev/null || true
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

start_hermes_bridge
start_hermes_openclaw
start_tunnel

log "Watchdog loop started"
TUNNEL_CHECK=0
LOG_ROTATION_COUNTER=0
OPENCLAW_FAIL_COUNT=0
OPENCLAW_BACKOFF=0

while true; do
    if [ "$OPENCLAW_BACKOFF" -gt 0 ]; then
        sleep "$OPENCLAW_BACKOFF"
        OPENCLAW_BACKOFF=0
    else
        sleep 2
    fi
    TUNNEL_CHECK=$((TUNNEL_CHECK + 1))
    LOG_ROTATION_COUNTER=$((LOG_ROTATION_COUNTER + 1))

    if [ $((LOG_ROTATION_COUNTER % 30)) -eq 0 ]; then
        rotate_logs
    fi

    if ! is_hermes_openclaw_running; then
        log "hermes-openclaw-bridge not running, restarting..."
        start_hermes_openclaw
        if [ "$OPENCLAW_FAIL_COUNT" -gt 0 ]; then
            if [ "$OPENCLAW_FAIL_COUNT" -lt 6 ]; then
                OPENCLAW_BACKOFF=$(( 2 ** OPENCLAW_FAIL_COUNT ))
            else
                OPENCLAW_BACKOFF=60
            fi
            log "Backing off ${OPENCLAW_BACKOFF}s (consecutive failures: $OPENCLAW_FAIL_COUNT)"
        fi
    else
        OPENCLAW_FAIL_COUNT=0
    fi

    if [ $((TUNNEL_CHECK % 15)) -eq 0 ]; then
        if ! is_hermes_bridge_running; then
            log "hermes-mcp-bridge not running, restarting..."
            start_hermes_bridge
        fi
        if [ $((TUNNEL_CHECK % 30)) -eq 0 ]; then
            if ! is_tunnel_running; then
                log "SSH tunnel down, restarting..."
                start_tunnel
            fi
        fi
    fi
done

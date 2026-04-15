#!/bin/bash
# bridge-claude/run-forever.sh
# 持久运行脚本：加载 claude-native 环境变量后启动 bridge-claude，崩溃自动重启
# 通过 crontab @reboot 或手动调用
#
# 用法:
#   bash run-forever.sh          # 前台运行（调试）
#   nohup bash run-forever.sh &  # 后台持久运行
#   bash run-forever.sh status   # 查询 bridge HTTP 状态与本地进程
#   bash run-forever.sh monitor  # 快速查看 status/events/errors
#   bash run-forever.sh env list # 列出可用 CC 环境脚本
#   bash run-forever.sh env current
#   bash run-forever.sh env use /mnt/c/Users/jimwa/cc_env_xxx.sh

BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BRIDGE_DIR/logs"
ENV_FILE="$BRIDGE_DIR/.env"
CC_ENV_SCRIPT="${CC_ENV_SCRIPT:-}"
CC_ENV_SELECTOR_FILE="$BRIDGE_DIR/.cc_env_script"
CC_ENV_GLOB="/mnt/c/Users/*/cc_env_*.sh"
SSH_TUNNEL_SCRIPT="$BRIDGE_DIR/start-ssh-tunnel.sh"

mkdir -p "$LOG_DIR"

resolve_cc_env_script() {
    local candidate=""
    local line=""

    # 1) shell env 最高优先级（适合手动临时切换）
    if [ -n "${CC_ENV_SCRIPT:-}" ] && [ -f "$CC_ENV_SCRIPT" ]; then
        echo "$CC_ENV_SCRIPT"
        return 0
    fi

    # 2) 持久选择文件（适合 systemd/重启后生效）
    if [ -f "$CC_ENV_SELECTOR_FILE" ]; then
        line="$(head -n 1 "$CC_ENV_SELECTOR_FILE" | tr -d '\r')"
        if [ -n "$line" ] && [ -f "$line" ]; then
            echo "$line"
            return 0
        fi
    fi

    # 3) 约定的“当前环境”文件（可由外部脚本维护）
    for candidate in /mnt/c/Users/*/cc_env_current.sh; do
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    # 4) 自动兜底：全局仅有一个 cc_env_*.sh 时自动选中
    set -- $CC_ENV_GLOB
    if [ "$1" != "$CC_ENV_GLOB" ] && [ "$#" -eq 1 ] && [ -f "$1" ]; then
        echo "$1"
        return 0
    fi

    return 1
}

show_env_list() {
    echo "[env] candidates in /mnt/c/Users/*:"
    ls -1 $CC_ENV_GLOB 2>/dev/null || echo "  (none)"
}

show_env_current() {
    local resolved=""
    resolved="$(resolve_cc_env_script || true)"
    echo "[env] selector file: $CC_ENV_SELECTOR_FILE"
    if [ -f "$CC_ENV_SELECTOR_FILE" ]; then
        echo "[env] selector value: $(head -n 1 "$CC_ENV_SELECTOR_FILE" | tr -d '\r')"
    else
        echo "[env] selector value: (not set)"
    fi
    if [ -n "$resolved" ]; then
        echo "[env] resolved: $resolved"
    else
        echo "[env] resolved: (none)"
    fi
}

use_env_script() {
    local target="$1"
    if [ -z "$target" ]; then
        echo "usage: bash run-forever.sh env use /mnt/c/Users/jimwa/cc_env_xxx.sh"
        return 1
    fi
    if [ ! -f "$target" ]; then
        echo "[env] file not found: $target"
        return 1
    fi
    printf '%s\n' "$target" > "$CC_ENV_SELECTOR_FILE"
    echo "[env] selected: $target"
}

resolve_status_port() {
    local port="${BRIDGE_STATUS_PORT:-}"
    if [ -n "$port" ]; then
        echo "$port"
        return 0
    fi

    if [ -f "$ENV_FILE" ]; then
        # 从 .env 中提取 BRIDGE_STATUS_PORT，格式：BRIDGE_STATUS_PORT=8765
        port="$(grep -E '^BRIDGE_STATUS_PORT=' "$ENV_FILE" | tail -n 1 | cut -d'=' -f2 | tr -d ' \r\"' || true)"
    fi

    echo "${port:-8765}"
}

show_status() {
    local status_port
    status_port="$(resolve_status_port)"

    echo "[status] bridge_dir=$BRIDGE_DIR"
    echo "[status] status_port=$status_port"
    echo "[status] run-forever processes:"
    pgrep -af "run-forever.sh" || echo "  (none)"
    echo "[status] bridge main processes:"
    pgrep -af "$BRIDGE_DIR/__main__\.py" || echo "  (none)"

    if command -v curl >/dev/null 2>&1; then
        echo "[status] http://127.0.0.1:${status_port}/status"
        curl -fsS "http://127.0.0.1:${status_port}/status" || echo "  (endpoint unavailable)"
        echo
    else
        echo "[status] curl not found, skip HTTP check"
    fi
}

show_monitor() {
    local status_port
    status_port="$(resolve_status_port)"

    echo "[monitor] status endpoint"
    if command -v curl >/dev/null 2>&1; then
        curl -sS "http://127.0.0.1:${status_port}/status" || echo "  (status unavailable)"
        echo
        echo "[monitor] recent events"
        curl -sS "http://127.0.0.1:${status_port}/events" || echo "  (events unavailable)"
        echo
        echo "[monitor] recent errors"
        curl -sS "http://127.0.0.1:${status_port}/errors" || echo "  (errors unavailable)"
        echo
    else
        echo "[monitor] curl not found"
    fi

    echo "[monitor] logs tail"
    tail -n 20 "$LOG_DIR/run-forever.log" 2>/dev/null || true
}

case "${1:-}" in
    status)
        show_status
        exit 0
        ;;
    monitor)
        show_monitor
        exit 0
        ;;
    env)
        case "${2:-}" in
            list)
                show_env_list
                exit 0
                ;;
            current)
                show_env_current
                exit 0
                ;;
            use)
                use_env_script "${3:-}"
                exit $?
                ;;
            *)
                echo "usage: bash run-forever.sh env {list|current|use <script_path>}"
                exit 1
                ;;
        esac
        ;;
esac

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/run-forever.log"
}

# ── 加载配置 ──────────────────────────────────────────────────────────────────

# 0. 先加载 bridge 配置，使 .env 可设置 CC_ENV_SCRIPT
if [ -f "$ENV_FILE" ]; then
    log "Loading bridge config from $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
else
    log "WARNING: $ENV_FILE not found, using defaults / environment"
fi

# 1. 加载 claude-native 所需的 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL 等
CC_ENV_SCRIPT="$(resolve_cc_env_script || true)"
if [ -n "$CC_ENV_SCRIPT" ] && [ -f "$CC_ENV_SCRIPT" ]; then
    log "Sourcing claude-native env from $CC_ENV_SCRIPT"
    # shellcheck disable=SC1090
    set -a
    source "$CC_ENV_SCRIPT"
    set +a
else
    log "WARNING: no valid CC env script selected; use 'bash run-forever.sh env use <path>'"
fi

log "run-forever started (pid=$$, bridge_dir=$BRIDGE_DIR)"
log "CLAWITH_API_URL=${CLAWITH_API_URL:-<not set>}"
log "MAX_CONCURRENT_TASKS=${MAX_CONCURRENT_TASKS:-2}"
log "BRIDGE_STATUS_PORT=${BRIDGE_STATUS_PORT:-8765}  (GET /status, POST /session/{conv_id}/decide)"

# ── 找到可用的 python3（需能导入 bridge 依赖）─────────────────────────────────

pick_python() {
    local candidates=()
    if [ -x "$BRIDGE_DIR/.venv/bin/python3" ]; then
        candidates+=("$BRIDGE_DIR/.venv/bin/python3")
    fi
    if [ -x "$BRIDGE_DIR/.venv/bin/python" ]; then
        candidates+=("$BRIDGE_DIR/.venv/bin/python")
    fi
    local sys_py
    sys_py="$(command -v python3 2>/dev/null || true)"
    if [ -n "$sys_py" ]; then
        candidates+=("$sys_py")
    fi

    for p in "${candidates[@]}"; do
        if "$p" -c "import claude_agent_sdk, anyio" >/dev/null 2>&1; then
            echo "$p"
            return 0
        fi
    done

    # 兜底：返回第一个可执行 python，后续由主程序给出缺包错误
    for p in "${candidates[@]}"; do
        if [ -x "$p" ]; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

PYTHON3="$(pick_python || true)"

if [ -z "$PYTHON3" ]; then
    log "ERROR: python3 not found in PATH"
    exit 1
fi
log "Using python3: $PYTHON3 ($(${PYTHON3} --version 2>&1))"

# ── 验证 claude-native 可执行文件 ─────────────────────────────────────────────

CLAUDE_BIN="$(command -v claude-native 2>/dev/null || true)"
if [ -z "$CLAUDE_BIN" ]; then
    log "WARNING: claude-native not found in PATH — claude_agent_sdk will still search its own PATH"
else
    log "claude-native found at: $CLAUDE_BIN"
fi

# ── Bridge 启动 / 检测 ────────────────────────────────────────────────────────

start_bridge() {
    if pgrep -f "$BRIDGE_DIR/__main__\.py" > /dev/null 2>&1; then
        log "bridge-claude already running, skipping start"
        return 0
    fi
    log "Starting bridge-claude..."
    setsid "$PYTHON3" "$BRIDGE_DIR/__main__.py" >> "$LOG_DIR/bridge.log" 2>&1 &
    echo $! > "$LOG_DIR/bridge.pid"
    log "bridge-claude started (pid=$!)"
    sleep 2
}

stop_bridge() {
    pkill -f "$BRIDGE_DIR/__main__\.py" 2>/dev/null || true
    rm -f "$LOG_DIR/bridge.pid"
    sleep 1
}

check_tunnel_health() {
    # Check tunnel connectivity only; HTTP status may be non-2xx depending on auth.
    curl -sS -m 3 -o /dev/null "http://127.0.0.1:8000/api/gateway/poll"
}

ensure_ssh_tunnel() {
    if [ ! -x "$SSH_TUNNEL_SCRIPT" ]; then
        log "WARNING: tunnel script not found or not executable: $SSH_TUNNEL_SCRIPT"
        return 1
    fi

    if check_tunnel_health; then
        return 0
    fi

    log "SSH tunnel down, restarting..."
    if bash "$SSH_TUNNEL_SCRIPT" >> "$LOG_DIR/run-forever.log" 2>&1; then
        local i
        for i in 1 2 3 4 5; do
            sleep 2
            if check_tunnel_health; then
                log "SSH tunnel ready"
                return 0
            fi
        done
    fi

    log "WARNING: SSH tunnel startup failed or still unhealthy"
    return 1
}

# ── 首次启动 ──────────────────────────────────────────────────────────────────

ensure_ssh_tunnel || true
start_bridge

# ── 看门狗主循环（每 10s 检查一次）──────────────────────────────────────────

log "Watchdog loop started (check every 10s)"
while true; do
    sleep 10

    ensure_ssh_tunnel || true

    if ! pgrep -f "$BRIDGE_DIR/__main__\.py" > /dev/null 2>&1; then
        log "bridge-claude not running, restarting..."

        # 重新 source 环境（ANTHROPIC_API_KEY 可能会滚动更新）
        CC_ENV_SCRIPT="$(resolve_cc_env_script || true)"
        if [ -n "$CC_ENV_SCRIPT" ] && [ -f "$CC_ENV_SCRIPT" ]; then
            set -a
            source "$CC_ENV_SCRIPT"
            set +a
        fi
        if [ -f "$ENV_FILE" ]; then
            set -a
            source "$ENV_FILE"
            set +a
        fi

        start_bridge
    fi
done

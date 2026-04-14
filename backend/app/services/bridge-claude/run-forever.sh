#!/bin/bash
# bridge-claude/run-forever.sh
# 持久运行脚本：加载 claude-native 环境变量后启动 bridge-claude，崩溃自动重启
# 通过 crontab @reboot 或手动调用
#
# 用法:
#   bash run-forever.sh          # 前台运行（调试）
#   nohup bash run-forever.sh &  # 后台持久运行

BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BRIDGE_DIR/logs"
ENV_FILE="$BRIDGE_DIR/.env"
CC_ENV_SCRIPT="/mnt/c/Users/jimwa/cc_env_nuoda.sh"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/run-forever.log"
}

# ── 加载配置 ──────────────────────────────────────────────────────────────────

# 1. 加载 claude-native 所需的 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL 等
if [ -f "$CC_ENV_SCRIPT" ]; then
    log "Sourcing claude-native env from $CC_ENV_SCRIPT"
    # shellcheck disable=SC1090
    set -a
    source "$CC_ENV_SCRIPT"
    set +a
else
    log "WARNING: $CC_ENV_SCRIPT not found, claude-native may fail to authenticate"
fi

# 2. 加载 bridge 自身配置（.env 里的变量会覆盖上面同名的 key，例如 ANTHROPIC_BASE_URL）
if [ -f "$ENV_FILE" ]; then
    log "Loading bridge config from $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
else
    log "WARNING: $ENV_FILE not found, using defaults / environment"
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

# ── 首次启动 ──────────────────────────────────────────────────────────────────

start_bridge

# ── 看门狗主循环（每 10s 检查一次）──────────────────────────────────────────

log "Watchdog loop started (check every 10s)"
while true; do
    sleep 10

    if ! pgrep -f "$BRIDGE_DIR/__main__\.py" > /dev/null 2>&1; then
        log "bridge-claude not running, restarting..."

        # 重新 source 环境（ANTHROPIC_API_KEY 可能会滚动更新）
        if [ -f "$CC_ENV_SCRIPT" ]; then
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

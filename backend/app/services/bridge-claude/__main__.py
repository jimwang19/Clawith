#!/usr/bin/env python3
"""
Clawith Bridge-Claude — 通过 claude_agent_sdk 连接 Claude Code CLI

架构:
  1. 轮询 Clawith 获取新消息
  2. 用 claude_agent_sdk.query() 异步流式调用 Claude Code CLI
  3. 流式推送中间状态（工具调用、回复中...）给 Clawith
  4. Claude 完成后 report 最终结果给 Clawith
  5. 如果 Claude 需要权限确认，转发给 Clawith 用户，等待回复后响应

需要: Python 3.10+, claude-agent-sdk, anyio
"""

import functools
import glob
import json
import os
import sys
import time
import random
import logging
import threading
from collections import deque
import urllib.request
import urllib.error
import uuid
import signal
import subprocess
from dataclasses import dataclass, field

try:
    import claude_agent_sdk
except ImportError:
    print("ERROR: claude_agent_sdk not installed. Run: pip install claude-agent-sdk")
    sys.exit(1)

try:
    import psutil
except ImportError:
    log = logging.getLogger("bridge-claude")
    log.warning("psutil not installed - subprocess cleanup will be limited")
    psutil = None

import anyio
from claude_agent_sdk import (
    query as claude_query,
    ClaudeAgentOptions,
    HookMatcher,
    AssistantMessage,
    TextBlock,
    ResultMessage,
    SystemMessage,
    RateLimitEvent,
)

# ── Logging ───────────────────────────────────────────────────────────────────

os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bridge-claude")

# ── Config ────────────────────────────────────────────────────────────────────

CLAWITH_API_URL          = os.environ.get("CLAWITH_API_URL",          "http://127.0.0.1:8000")
CLAWITH_API_KEY          = os.environ.get("CLAWITH_API_KEY",          "")
POLL_INTERVAL            = int(os.environ.get("POLL_INTERVAL",        "5"))
TASK_TIMEOUT             = int(os.environ.get("TASK_TIMEOUT",     "300"))
CLAWITH_SEND_ENABLED     = os.environ.get("CLAWITH_SEND_ENABLED",     "0") == "1"
IDLE_POLL_LOG_EVERY      = int(os.environ.get("IDLE_POLL_LOG_EVERY",  "12"))
PROGRESS_VIA_REPORT      = os.environ.get("PROGRESS_VIA_REPORT",      "1") == "1"
INFLIGHT_RECOVER_MAX_AGE = int(os.environ.get("INFLIGHT_RECOVER_MAX_AGE", "900"))
HTTP_TIMEOUT_SECONDS     = int(os.environ.get("HTTP_TIMEOUT_SECONDS", "10"))
HTTP_MAX_RETRIES         = int(os.environ.get("HTTP_MAX_RETRIES", "2"))
HTTP_RETRY_BASE_DELAY    = float(os.environ.get("HTTP_RETRY_BASE_DELAY", "0.7"))
HTTP_RETRY_MAX_DELAY     = float(os.environ.get("HTTP_RETRY_MAX_DELAY", "5"))
HTTP_RETRY_JITTER        = float(os.environ.get("HTTP_RETRY_JITTER", "0.25"))
HTTP_ALERT_CONSEC_FAILS  = int(os.environ.get("HTTP_ALERT_CONSEC_FAILS", "5"))

CLAUDE_WORKDIR_BASE      = os.environ.get("CLAUDE_WORKDIR_BASE",      "/workspaces")
CLAUDE_MODEL             = os.environ.get("CLAUDE_MODEL",             "")
CLAUDE_MAX_TURNS         = int(os.environ.get("CLAUDE_MAX_TURNS",     "50"))
CLAUDE_PERMISSION_MODE   = os.environ.get("CLAUDE_PERMISSION_MODE",   "default")
CLAUDE_ALLOWED_TOOLS     = os.environ.get("CLAUDE_ALLOWED_TOOLS",     "")
CC_ENV_SCRIPT            = os.environ.get("CC_ENV_SCRIPT",            "")

SELF_AGENT_NAME          = os.environ.get("BRIDGE_AGENT_NAME",        "cc-agent")

INFLIGHT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "inflight.json")
STATUS_PORT              = int(os.environ.get("BRIDGE_STATUS_PORT", "8765"))
MAX_CONCURRENT_TASKS     = int(os.environ.get("MAX_CONCURRENT_TASKS", "2"))
MONITOR_EVENTS_MAX       = int(os.environ.get("MONITOR_EVENTS_MAX", "200"))
MONITOR_ERROR_MAX        = int(os.environ.get("MONITOR_ERROR_MAX", "100"))

_http_fail_streak = 0
_http_streak_lock = threading.Lock()

_monitor_lock = threading.Lock()
_monitor_events = deque(maxlen=MONITOR_EVENTS_MAX)
_monitor_errors = deque(maxlen=MONITOR_ERROR_MAX)
_monitor_stats = {
    "started_at": int(time.time()),
    "messages_received": 0,
    "tasks_started": 0,
    "tasks_succeeded": 0,
    "tasks_failed": 0,
    "busy_rejected": 0,
    "concurrency_rejected": 0,
    "permission_requests": 0,
    "permission_denied": 0,
    "permission_allowed": 0,
    "exceptions": 0,
}


# ── Short text helper ─────────────────────────────────────────────────────────

def _short_text(text: str, limit: int = 120) -> str:
    """单行摘要，便于日志追踪。"""
    s = (text or "").replace("\n", " ").strip()
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def _looks_like_non_task_echo(text: str) -> bool:
    """Heuristic filter for non-actionable loop chatter from peer agents."""
    s = (text or "").strip().lower()
    if not s:
        return True

    markers = (
        "no task",
        "standing by",
        "deadlock",
        "echo loop",
        "absolute silence",
        "no response",
        "real task",
        "waiting for",
        "staying silent",
    )
    if any(m in s for m in markers):
        return True

    # Placeholder-only payloads like "..." or "***" are non-task inputs.
    compact = s.replace(".", "").replace("*", "").replace("-", "").strip()
    return compact == ""


def _monitor_inc(key: str, delta: int = 1):
    with _monitor_lock:
        _monitor_stats[key] = int(_monitor_stats.get(key, 0) or 0) + delta


def _monitor_event(event: str, level: str = "info", **kwargs):
    entry = {
        "ts": int(time.time()),
        "event": event,
        "level": level,
    }
    entry.update({k: v for k, v in kwargs.items() if v is not None})

    with _monitor_lock:
        _monitor_events.append(entry)
        if level in {"warning", "error"}:
            _monitor_errors.append(entry)
        if level == "error":
            _monitor_stats["exceptions"] = int(_monitor_stats.get("exceptions", 0) or 0) + 1


def _monitor_snapshot(include_events: bool = False, include_errors: bool = False) -> dict:
    with _monitor_lock:
        stats = dict(_monitor_stats)
        events = list(_monitor_events) if include_events else None
        errors = list(_monitor_errors) if include_errors else None

    body = {
        "stats": stats,
        "uptime_s": max(0, int(time.time()) - int(stats.get("started_at", int(time.time())))),
    }
    if events is not None:
        body["events"] = events
    if errors is not None:
        body["errors"] = errors
    return body


def _resolve_cc_env_script() -> str:
    """Resolve CC env script path without hardcoded user-specific file names."""
    # 1) explicit env var has highest priority
    explicit = os.environ.get("CC_ENV_SCRIPT", "").strip()
    if explicit and os.path.exists(explicit):
        return explicit

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # 2) persistent selector file next to runtime scripts
    selector_file = os.path.join(base_dir, ".cc_env_script")
    if os.path.exists(selector_file):
        try:
            with open(selector_file, "r", encoding="utf-8") as f:
                selected = (f.readline() or "").strip()
            if selected and os.path.exists(selected):
                return selected
        except Exception:
            pass

    # 3) optional conventional alias file under any Windows user
    current_aliases = sorted(glob.glob("/mnt/c/Users/*/cc_env_current.sh"))
    if current_aliases:
        return current_aliases[0]

    # 4) final fallback when there is exactly one env script globally
    all_candidates = sorted(glob.glob("/mnt/c/Users/*/cc_env_*.sh"))
    if len(all_candidates) == 1:
        return all_candidates[0]

    return ""


def _load_claude_env():
    """加载 Claude Code 运行环境脚本（WSL 用）。"""
    script = _resolve_cc_env_script()
    if not script:
        log.debug("[env] no CC env script resolved")
        return

    if not CC_ENV_SCRIPT or not os.path.exists(CC_ENV_SCRIPT):
        # Keep env in sync for downstream code that reads CC_ENV_SCRIPT.
        os.environ["CC_ENV_SCRIPT"] = script
    
    try:
        log.info(f"[env] sourcing CC env script: {script}")
        result = subprocess.run(
            ["bash", "-c", f"set -a; source '{script}'; set +a; env"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # 解析环境变量
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key] = value
            log.info(f"[env] CC environment loaded successfully")
        else:
            log.warning(f"[env] failed to source CC env script: {result.stderr}")
    except subprocess.TimeoutExpired:
        log.error("[env] timeout sourcing CC env script")
    except Exception as e:
        log.error(f"[env] error loading CC env script: {e}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _next_retry_delay(attempt: int) -> float:
    # Exponential backoff with jitter to smooth burst failures on unstable links.
    base = min(HTTP_RETRY_MAX_DELAY, HTTP_RETRY_BASE_DELAY * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0, HTTP_RETRY_JITTER)
    return min(HTTP_RETRY_MAX_DELAY, base + jitter)


def _mark_http_success():
    global _http_fail_streak
    with _http_streak_lock:
        previous = _http_fail_streak
        _http_fail_streak = 0
    if previous >= HTTP_ALERT_CONSEC_FAILS:
        log.warning(f"[net] recovered after {previous} consecutive HTTP failure(s)")


def _mark_http_failure(status_hint: str):
    global _http_fail_streak
    with _http_streak_lock:
        _http_fail_streak += 1
        streak = _http_fail_streak
    if streak == HTTP_ALERT_CONSEC_FAILS or (
        HTTP_ALERT_CONSEC_FAILS > 0 and streak > HTTP_ALERT_CONSEC_FAILS and streak % HTTP_ALERT_CONSEC_FAILS == 0
    ):
        log.warning(f"[net] {streak} consecutive HTTP failure(s), latest={status_hint}")


def _http(method: str, url: str, data=None, headers=None, timeout=None, retries=None):
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    timeout = HTTP_TIMEOUT_SECONDS if timeout is None else timeout
    retries = HTTP_MAX_RETRIES if retries is None else max(0, int(retries))

    for attempt in range(1, retries + 2):
        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                _mark_http_success()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            retryable = e.code in {408, 429, 500, 502, 503, 504}
            if retryable and attempt <= retries:
                delay = _next_retry_delay(attempt)
                log.warning(
                    f"HTTP {e.code} {method} {url} attempt={attempt}/{retries + 1}, retry in {delay:.2f}s: {raw[:120]}"
                )
                time.sleep(delay)
                continue
            log.error(f"HTTP {e.code} {method} {url}: {raw[:200]}")
            _mark_http_failure(str(e.code))
            return e.code, {}
        except Exception as e:
            if attempt <= retries:
                delay = _next_retry_delay(attempt)
                log.warning(
                    f"Request failed {method} {url} attempt={attempt}/{retries + 1}, retry in {delay:.2f}s: {e}"
                )
                time.sleep(delay)
                continue
            log.error(f"Request failed {method} {url}: {e}")
            _mark_http_failure(type(e).__name__)
            return 0, {}

    _mark_http_failure("unknown")
    return 0, {}


# ── Clawith API ───────────────────────────────────────────────────────────────

def clawith_poll():
    status, body = _http("GET", f"{CLAWITH_API_URL}/api/gateway/poll",
        headers={"X-Api-Key": CLAWITH_API_KEY})
    if status == 200:
        return body.get("messages", [])
    return []

def clawith_report(message_id: str, result: str):
    message_id = str(message_id or "")
    # Convert non-UUID message_id to UUID format for Clawith API
    # Clawith message IDs like "34876783" need to be padded to UUID format
    if len(message_id) < 36 and "-" not in message_id:
        # Pad to UUID: 00000000-0000-0000-0000-XXXXXXXXXXXX
        uuid_id = f"00000000-0000-0000-0000-{message_id.zfill(12)}"
        log.debug(f"[report] converting {message_id} -> {uuid_id}")
    else:
        uuid_id = message_id

    status, body = _http("POST", f"{CLAWITH_API_URL}/api/gateway/report",
        data={"message_id": uuid_id, "result": result},
        headers={"X-Api-Key": CLAWITH_API_KEY},
        retries=1)
    if status == 200:
        log.info(f"[report] ok msg={message_id} uuid={uuid_id} text='{_short_text(result, 160)}'")
        return True
    elif status == 422:
        log.error(f"[report] 422 UUID error for {message_id} (tried {uuid_id})")
        return False
    else:
        log.error(f"[report] failed status={status} msg={message_id} text='{_short_text(result, 120)}'")
        return False

def clawith_send_message(conversation_id: str, content: str):
    """向 Clawith 对话发送中间状态消息"""
    if not CLAWITH_SEND_ENABLED:
        log.info(f"[send] skipped conv={conversation_id[:8]} text='{_short_text(content)}'")
        return False
    status, _ = _http("POST", f"{CLAWITH_API_URL}/api/gateway/send",
        data={"conversation_id": conversation_id, "content": content},
        headers={"X-Api-Key": CLAWITH_API_KEY},
        retries=1)
    if status == 200:
        log.info(f"[send] conv={conversation_id[:8]} text='{_short_text(content)}'")
    else:
        log.warning(f"[send] failed status={status} conv={conversation_id[:8]} text='{_short_text(content)}'")
    return status == 200

def clawith_heartbeat():
    _http("POST", f"{CLAWITH_API_URL}/api/gateway/heartbeat",
        headers={"X-Api-Key": CLAWITH_API_KEY})


# ── Inflight task persistence ─────────────────────────────────────────────────

_inflight_lock = threading.Lock()

def _load_inflight() -> dict:
    try:
        with open(INFLIGHT_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_inflight(data: dict):
    tmp = INFLIGHT_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, INFLIGHT_FILE)
    except Exception as e:
        log.warning(f"[inflight] save failed: {e}")

def inflight_add(msg_id: str, content: str, conv_id: str):
    with _inflight_lock:
        data = _load_inflight()
        data[str(msg_id)] = {"content": content[:200], "conv_id": str(conv_id), "ts": time.time()}
        _save_inflight(data)

def inflight_remove(msg_id: str):
    with _inflight_lock:
        data = _load_inflight()
        if str(msg_id) in data:
            del data[str(msg_id)]
            _save_inflight(data)

def recover_inflight():
    """启动时：对上次中断的 in-flight 任务回报中断信息，让 Clawith 侧感知到 bridge 重启。"""
    with _inflight_lock:
        data = _load_inflight()
    if not data:
        return
    now = time.time()
    recent = {
        msg_id: info
        for msg_id, info in data.items()
        if now - float(info.get("ts", 0) or 0) <= INFLIGHT_RECOVER_MAX_AGE
    }
    stale_count = len(data) - len(recent)
    if stale_count:
        log.warning(f"[startup] skip {stale_count} stale inflight task(s)")
    if not recent:
        with _inflight_lock:
            _save_inflight({})
        return

    log.warning(f"[startup] found {len(recent)} recent inflight task(s), reporting interruption")
    for msg_id, info in recent.items():
        preview = info.get("content", "")[:60].replace("\n", " ")
        clawith_report(
            msg_id,
            f"⚠️ Bridge 重启，任务中断（上次任务：{preview}）。请重新发送消息。"
        )
    with _inflight_lock:
        _save_inflight({})
    log.info(f"[startup] cleared {len(data)} inflight task(s)")


# ── Session management ────────────────────────────────────────────────────────

# conv_id -> claude_session_id (populated at runtime from SystemMessage init)
_sessions: dict[str, str] = {}

def get_workdir(conv_id: str) -> str:
    return os.path.join(CLAUDE_WORKDIR_BASE, str(conv_id))


# ── TaskState ─────────────────────────────────────────────────────────────────

@dataclass
class TaskState:
    clawith_msg_id: str
    conv_id: str
    claude_session_id: str          # may be empty initially
    request_preview: str
    sender_is_agent: bool
    reported: bool = False
    start_time: float = field(default_factory=time.time)
    last_status_push: float = 0.0
    last_progress_text: str = ""
    pending_permission: threading.Event = field(default_factory=threading.Event)
    permission_decision: str = "deny"
    tool_calls: list = field(default_factory=list)
    _waiting_permission: bool = False
    thread: threading.Thread | None = None        # Store thread reference for join()
    child_pids: list = field(default_factory=list)  # Track spawned child processes


# ── Active tasks dicts ────────────────────────────────────────────────────────

_active_tasks: dict[str, TaskState] = {}       # session_id -> TaskState
_active_conv_tasks: dict[str, TaskState] = {}  # conv_id -> TaskState
_all_tasks: list[TaskState] = []               # Track all tasks for graceful shutdown
_tasks_lock = threading.Lock()

# ── Shutdown signal ───────────────────────────────────────────────────────────

_shutdown_event = threading.Event()

# ── Concurrency slot management ───────────────────────────────────────────────

_concurrent_count = 0
_concurrent_lock = threading.Lock()


def try_acquire_slot(task: TaskState) -> bool:
    """尝试获取并发槽位。成功返回 True，超限返回 False。"""
    global _concurrent_count
    with _concurrent_lock:
        if _concurrent_count >= MAX_CONCURRENT_TASKS:
            return False
        _concurrent_count += 1
    with _tasks_lock:
        _active_tasks[task.conv_id] = task
        _active_conv_tasks[task.conv_id] = task
    return True


def release_slot(task: TaskState):
    """释放并发槽位并清理子进程。"""
    global _concurrent_count
    
    # 清理子进程
    _cleanup_child_processes(task)
    
    with _concurrent_lock:
        _concurrent_count = max(0, _concurrent_count - 1)
    with _tasks_lock:
        _active_tasks.pop(task.claude_session_id, None)
        _active_tasks.pop(task.conv_id, None)
        if _active_conv_tasks.get(task.conv_id) is task:
            _active_conv_tasks.pop(task.conv_id, None)


def _cleanup_child_processes(task: TaskState):
    """清理任务关联的子进程（Claude Code CLI 及其子进程）。"""
    if not task.child_pids:
        return
    
    for pid in task.child_pids:
        try:
            if psutil:
                try:
                    proc = psutil.Process(pid)
                    if proc.is_running():
                        # Try graceful termination first
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                            log.info(f"[cleanup] gracefully terminated child pid={pid}")
                        except subprocess.TimeoutExpired:
                            # Force kill if not terminated
                            proc.kill()
                            proc.wait(timeout=2)
                            log.warning(f"[cleanup] force-killed child pid={pid}")
                except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                    log.debug(f"[cleanup] process {pid} already dead or inaccessible: {e}")
            else:
                # Fallback: try os.kill if psutil unavailable
                try:
                    os.kill(pid, signal.SIGTERM)
                    log.info(f"[cleanup] sent SIGTERM to child pid={pid}")
                except ProcessLookupError:
                    log.debug(f"[cleanup] process {pid} not found")
        except Exception as e:
            log.error(f"[cleanup] error terminating pid {pid}: {e}")
    
    task.child_pids.clear()


# ── Push status ───────────────────────────────────────────────────────────────

def _push_status(task: TaskState, message: str):
    """向 Clawith 推送中间状态，限流 3s/次"""
    now = time.time()
    if now - task.last_status_push < 3:
        return
    task.last_status_push = now

    # 避免同一状态反复刷屏
    if message == task.last_progress_text:
        return
    task.last_progress_text = message

    try:
        if CLAWITH_SEND_ENABLED:
            ok = clawith_send_message(task.conv_id, message)
            if not ok:
                log.debug("[push] send API unavailable or failed")
            return

        # send 通道关闭时，针对 agent-to-agent 会话使用多次 report 做进度更新
        if PROGRESS_VIA_REPORT and task.sender_is_agent and not task.reported:
            progress_text = f"⏳ {message}"
            clawith_report(task.clawith_msg_id, progress_text)
            log.info(
                f"[push] progress_report msg={str(task.clawith_msg_id)[:8]} "
                f"session={task.claude_session_id[:8] if task.claude_session_id else 'none'} "
                f"text='{_short_text(progress_text)}'"
            )
    except Exception as e:
        log.debug(f"[push] error: {e}")


# ── Task completion helpers ───────────────────────────────────────────────────

def _finish_task(task: TaskState, result: str):
    if task.reported:
        return
    task.reported = True
    release_slot(task)
    elapsed = int(time.time() - task.start_time)
    log.info(f"[done] session={task.claude_session_id[:8] if task.claude_session_id else 'none'} ({elapsed}s)")
    _monitor_inc("tasks_succeeded", 1)
    _monitor_event(
        "task_finished",
        level="info",
        msg_id=str(task.clawith_msg_id),
        conv_id=str(task.conv_id),
        elapsed_s=elapsed,
    )
    inflight_remove(str(task.clawith_msg_id))
    clawith_report(task.clawith_msg_id, result)

def _fail_task(task: TaskState, reason: str):
    _monitor_inc("tasks_failed", 1)
    _monitor_event(
        "task_failed",
        level="error",
        msg_id=str(task.clawith_msg_id),
        conv_id=str(task.conv_id),
        reason=_short_text(reason, 240),
    )
    _finish_task(task, f"❌ {reason}")


# ── High-risk tool check ──────────────────────────────────────────────────────

_HIGH_RISK_TOOLS = {"Bash", "Write", "Edit"}

def _is_high_risk_tool(name: str) -> bool:
    return name in _HIGH_RISK_TOOLS


# ── Permission hook factory ───────────────────────────────────────────────────

def _make_permission_hook(task: TaskState):
    async def hook(input_data, tool_use_id, context):
        # PreToolUseHookInput: tool_name is a top-level field, tool_input contains args
        tool_name = input_data.get("tool_name") or "unknown"
        tool_input = input_data.get("tool_input", {})

        # Record the tool call
        task.tool_calls.append(tool_name)
        log.info(f"  [tool] {tool_name}")
        _monitor_event(
            "tool_call",
            level="info",
            msg_id=str(task.clawith_msg_id),
            conv_id=str(task.conv_id),
            tool=tool_name,
        )
        _push_status(task, f"正在调用工具: {tool_name}...")

        # Only intercept in default permission mode for high-risk tools
        if CLAUDE_PERMISSION_MODE != "default" or not _is_high_risk_tool(tool_name):
            return {}

        # 准备权限请求消息
        args_str = json.dumps(tool_input, ensure_ascii=False)[:200]
        msg = (
            f"⚠️ **Claude Code 请求权限确认**\n"
            f"工具: `{tool_name}`\n"
            + (f"参数: `{args_str}`\n" if args_str else "")
            + "\n请回复 **允许** 或 **拒绝**，或调用 /session/{task.conv_id}/decide"
        )
        task.pending_permission.clear()
        task.last_progress_text = ""
        task.last_status_push = 0
        task._waiting_permission = True
        _monitor_inc("permission_requests", 1)
        _monitor_event(
            "permission_requested",
            level="warning",
            msg_id=str(task.clawith_msg_id),
            conv_id=str(task.conv_id),
            tool=tool_name,
        )

        # 有 send 通道则推送给 Clawith 用户；否则仅通过 /decide 接口等待
        if CLAWITH_SEND_ENABLED:
            _push_status(task, msg)
        else:
            log.info(f"[perm] waiting for /decide on conv={task.conv_id[:8]} tool={tool_name}")

        # 阻塞等待决策（120s 超时）
        granted = await anyio.to_thread.run_sync(
            functools.partial(task.pending_permission.wait, 120)
        )
        task._waiting_permission = False
        if not granted:
            log.warning(f"[perm] timeout waiting for decision on {tool_name}, auto-deny")
            task.permission_decision = "deny"

        if task.permission_decision == "allow":
            log.info(f"[perm] allowed: {tool_name}")
            _monitor_inc("permission_allowed", 1)
            _monitor_event(
                "permission_allowed",
                level="info",
                msg_id=str(task.clawith_msg_id),
                conv_id=str(task.conv_id),
                tool=tool_name,
            )
            return {}
        log.info(f"[perm] denied: {tool_name}")
        _monitor_inc("permission_denied", 1)
        _monitor_event(
            "permission_denied",
            level="warning",
            msg_id=str(task.clawith_msg_id),
            conv_id=str(task.conv_id),
            tool=tool_name,
        )
        return {"decision": "block"}

    return hook


# ── Permission reply handler ──────────────────────────────────────────────────

def _handle_permission_reply(content: str, conv_id: str) -> bool:
    c = content.strip()
    allow = c in ("允许", "allow", "yes", "y", "ok", "确认", "同意")
    deny  = c in ("拒绝", "deny", "no", "n", "不", "否", "不允许")

    if not allow and not deny:
        return False

    with _tasks_lock:
        tasks_snapshot = [t for t in _active_tasks.values() if t.conv_id == conv_id]

    for task in tasks_snapshot:
        if task.pending_permission.is_set():
            continue  # already resolved
        task.permission_decision = "allow" if allow else "deny"
        task.pending_permission.set()
        log.info(f"[perm] reply={task.permission_decision} conv={conv_id[:8]}")
        return True

    return False


# ── Core async Claude query ───────────────────────────────────────────────────

async def _claude_query(task: TaskState, content: str):
    # 加载 Claude Code 环境（仅需一次，但多次调用是安全的）
    _load_claude_env()
    
    workdir = get_workdir(task.conv_id)
    try:
        os.makedirs(workdir, exist_ok=True)
    except Exception as e:
        _fail_task(task, f"无法创建工作目录 {workdir}: {e}")
        return

    # Capture current process's children before spawning Claude
    pid_before = set()
    if psutil:
        try:
            current_proc = psutil.Process()
            pid_before = {p.pid for p in current_proc.children(recursive=True)}
        except Exception:
            pass

    kwargs = dict(
        cwd=workdir,
        permission_mode=CLAUDE_PERMISSION_MODE,
        max_turns=CLAUDE_MAX_TURNS,
        hooks={
            "PreToolUse": [HookMatcher(matcher="Bash|Write|Edit", hooks=[_make_permission_hook(task)])]
        },
    )
    if task.claude_session_id:
        kwargs["resume"] = task.claude_session_id
    if CLAUDE_MODEL:
        kwargs["model"] = CLAUDE_MODEL
    if CLAUDE_ALLOWED_TOOLS:
        kwargs["allowed_tools"] = [t.strip() for t in CLAUDE_ALLOWED_TOOLS.split(",") if t.strip()]

    options = ClaudeAgentOptions(**kwargs)

    try:
        async for msg in claude_query(prompt=content, options=options):
            if task.reported:
                break

            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                sid = msg.data.get("session_id") if isinstance(msg.data, dict) else None
                if sid:
                    task.claude_session_id = sid
                    _sessions[task.conv_id] = sid
                    with _tasks_lock:
                        # Remove old conv_id key, add real session_id key
                        _active_tasks.pop(task.conv_id, None)
                        _active_tasks[sid] = task
                    log.info(f"[session] new session={sid[:8]} conv={task.conv_id[:8]}")

            elif isinstance(msg, AssistantMessage):
                for block in (msg.content or []):
                    if isinstance(block, TextBlock) and block.text.strip():
                        _push_status(task, "AI 正在回复...")

            elif isinstance(msg, ResultMessage):
                # Capture new child processes spawned by Claude
                if psutil:
                    try:
                        current_proc = psutil.Process()
                        pid_after = {p.pid for p in current_proc.children(recursive=True)}
                        new_pids = pid_after - pid_before
                        task.child_pids.extend(new_pids)
                        if new_pids:
                            log.info(f"[claude] captured {len(new_pids)} child processes: {new_pids}")
                    except Exception as e:
                        log.debug(f"[claude] error capturing child pids: {e}")
                
                if msg.is_error:
                    result = f"❌ Claude Code 执行出错\n\n{msg.result or '(无详情)'}"
                else:
                    result = msg.result or "(无回复)"
                    if task.tool_calls:
                        tools = ", ".join(dict.fromkeys(task.tool_calls))
                        result = f"*(调用了: {tools})*\n\n{result}"
                _finish_task(task, result)
                return

            elif isinstance(msg, RateLimitEvent):
                _monitor_event(
                    "rate_limited",
                    level="warning",
                    msg_id=str(task.clawith_msg_id),
                    conv_id=str(task.conv_id),
                )
                _push_status(task, "⏳ API 限流，等待重置...")

    except Exception as e:
        log.error(f"[claude] error: {e}", exc_info=True)
        _monitor_event(
            "claude_query_exception",
            level="error",
            msg_id=str(task.clawith_msg_id),
            conv_id=str(task.conv_id),
            error=_short_text(str(e), 240),
        )
        if not task.reported:
            _fail_task(task, f"Claude Code 执行出错: {e}")
        return

    # Generator exhausted without ResultMessage
    if not task.reported:
        _fail_task(task, "Claude Code 未返回结果（生成器耗尽）")


# ── Thread entry point ────────────────────────────────────────────────────────

def _run_claude_task(task: TaskState, content: str):
    try:
        anyio.run(_claude_query, task, content)
    except Exception as e:
        log.error(f"[thread] anyio.run error: {e}", exc_info=True)
        _monitor_event(
            "thread_exception",
            level="error",
            msg_id=str(task.clawith_msg_id),
            conv_id=str(task.conv_id),
            error=_short_text(str(e), 240),
        )
        if not task.reported:
            _fail_task(task, f"任务线程异常: {e}")
    finally:
        # Ensure child processes are cleaned up even if exception occurred
        _cleanup_child_processes(task)
        log.info(f"[thread] {task.clawith_msg_id} cleanup completed")


# ── Message processing ────────────────────────────────────────────────────────

def process_message(msg: dict):
    msg_id  = msg.get("id")
    content = msg.get("content", "")
    conv_id = msg.get("conversation_id") or msg_id
    sender  = msg.get("sender_user_name") or msg.get("sender_agent_name") or "user"

    log.info(
        f"[msg] recv id={str(msg_id)[:8]} conv={str(conv_id)[:8]} "
        f"sender='{sender}' text='{_short_text(content)}'"
    )
    _monitor_inc("messages_received", 1)
    _monitor_event(
        "message_received",
        level="info",
        msg_id=str(msg_id),
        conv_id=str(conv_id),
        sender=sender,
        text=_short_text(content, 180),
    )

    # 忽略自身发出的 report 回流消息（防止 busy 拒绝消息形成雪崩循环）
    sender_agent = msg.get("sender_agent_name") or ""
    if sender_agent.lower() == SELF_AGENT_NAME.lower():
        log.debug(f"[msg] drop self-echo id={str(msg_id)[:8]} sender='{sender_agent}'")
        return

    # Drop non-task chatter from other agents to avoid echo/deadlock loops
    # continuously occupying the same conversation slot.
    if sender_agent and _looks_like_non_task_echo(content):
        log.info(
            f"[msg] drop non-task echo id={str(msg_id)[:8]} "
            f"sender='{sender_agent}' text='{_short_text(content)}'"
        )
        return

    # Check if this is a permission reply
    if _handle_permission_reply(content, conv_id):
        clawith_report(msg_id, "✅ 已处理权限请求")
        return

    # ── /new-session 命令：清除当前 conv 的 session，下一条消息开启全新对话 ──
    stripped = content.strip()
    if stripped in ("/new-session", "/新会话", "/reset"):
        old = _sessions.pop(conv_id, None)
        if old:
            clawith_report(msg_id, f"✅ 已清除会话（原 session `{old[:8]}...`），下一条消息将开启全新 Claude 会话。")
            log.info(f"[session] reset conv={str(conv_id)[:8]} old_session={old[:8]}")
        else:
            clawith_report(msg_id, "ℹ️ 当前没有已保存的会话，下一条消息本就会开启全新对话。")
            log.info(f"[session] reset conv={str(conv_id)[:8]} (no session to clear)")
        return

    # Reject if same conv already has a running task
    with _tasks_lock:
        running = _active_conv_tasks.get(conv_id)
        if running and not running.reported:
            elapsed = int(time.time() - running.start_time)
            preview = (running.request_preview or "(上一个任务)").replace("\n", " ")[:80]
            clawith_report(
                msg_id,
                f"⏳ 前面有任务还在处理（已进行 {elapsed}s）\n"
                f"当前任务: {preview}\n"
                "请等待该任务完成后再发送新消息。"
            )
            _monitor_inc("busy_rejected", 1)
            _monitor_event(
                "busy_reject",
                level="warning",
                msg_id=str(msg_id),
                conv_id=str(conv_id),
                elapsed_s=elapsed,
            )
            return

    # Get or derive session_id
    existing_session_id = _sessions.get(conv_id, "")

    # Create TaskState
    task = TaskState(
        clawith_msg_id=msg_id,
        conv_id=conv_id,
        claude_session_id=existing_session_id,
        request_preview=content,
        sender_is_agent=bool(msg.get("sender_agent_name")),
    )

    # Try to acquire a concurrency slot (global limit MAX_CONCURRENT_TASKS)
    if not try_acquire_slot(task):
        log.warning(f"[concurrency] 429 rejected msg={str(msg_id)[:8]} conv={str(conv_id)[:8]}")
        clawith_report(
            msg_id,
            f"429 并发任务已达上限（最多 {MAX_CONCURRENT_TASKS} 个），请稍后重试。"
        )
        _monitor_inc("concurrency_rejected", 1)
        _monitor_event(
            "concurrency_reject",
            level="warning",
            msg_id=str(msg_id),
            conv_id=str(conv_id),
            max_concurrent=MAX_CONCURRENT_TASKS,
        )
        return
    inflight_add(str(msg_id), content, str(conv_id))

    log.info(
        f"[route] msg={str(msg_id)[:8]} conv={str(conv_id)[:8]} "
        f"session='{existing_session_id[:8] if existing_session_id else 'new'}' "
        f"({'resume' if existing_session_id else 'new-session'})"
    )
    _monitor_inc("tasks_started", 1)
    _monitor_event(
        "task_started",
        level="info",
        msg_id=str(msg_id),
        conv_id=str(conv_id),
        session=(existing_session_id[:8] if existing_session_id else "new"),
        mode=("resume" if existing_session_id else "new-session"),
    )

    # Spawn thread (non-daemon to ensure proper cleanup)
    t = threading.Thread(
        target=_run_claude_task,
        args=(task, content),
        daemon=False,  # Non-daemon to allow graceful shutdown
        name=f"task-{str(msg_id)[:8]}",
    )
    task.thread = t  # Store reference for join()
    with _tasks_lock:
        _all_tasks.append(task)
    t.start()


# ── Graceful shutdown ────────────────────────────────────────────────────────

def _cleanup_all_tasks():
    """在关闭时等待并清理所有任务。"""
    log.info("[shutdown] initiating graceful shutdown...")
    _shutdown_event.set()
    
    with _tasks_lock:
        tasks_snapshot = list(_all_tasks)
    
    # First, terminate any Claude subprocesses
    for task in tasks_snapshot:
        if not task.reported and task.child_pids:
            log.info(f"[shutdown] cleaning up child processes for task {str(task.clawith_msg_id)[:8]}")
            _cleanup_child_processes(task)
    
    # Wait for all task threads to finish (with timeout)
    for task in tasks_snapshot:
        if task.thread and task.thread.is_alive():
            timeout = min(10, TASK_TIMEOUT // 5)  # Wait max 10s per thread
            log.info(f"[shutdown] waiting for thread {task.thread.name} (timeout={timeout}s)")
            task.thread.join(timeout=timeout)
            if task.thread.is_alive():
                log.warning(f"[shutdown] thread {task.thread.name} did not finish after {timeout}s")
    
    log.info("[shutdown] graceful shutdown complete")


def _signal_handler(signum, frame):
    """处理 SIGTERM/SIGINT 信号。"""
    log.info(f"[signal] received signal {signum}, initiating shutdown")
    _cleanup_all_tasks()
    sys.exit(0)


# ── Timeout monitor ───────────────────────────────────────────────────────────

def timeout_monitor():
    while not _shutdown_event.is_set():
        time.sleep(10)
        now = time.time()
        with _tasks_lock:
            timed_out = [
                t for t in _active_tasks.values()
                if not t.reported and now - t.start_time > TASK_TIMEOUT
            ]
        for task in timed_out:
            log.warning(f"[timeout] msg={str(task.clawith_msg_id)[:8]} after {TASK_TIMEOUT}s")
            _fail_task(task, f"任务超时（超过 {TASK_TIMEOUT}s）")


# ── Status HTTP server ────────────────────────────────────────────────────────

import http.server

def _task_status_str(task: TaskState) -> str:
    if task._waiting_permission:
        return "waiting_permission"
    return "running"

def _task_to_dict(task: TaskState) -> dict:
    return {
        "msg_id":           str(task.clawith_msg_id),
        "conv_id":          str(task.conv_id),
        "status":           _task_status_str(task),
        "elapsed_s":        int(time.time() - task.start_time),
        "tool_calls":       list(task.tool_calls),
        "last_progress":    task.last_progress_text,
        "request_preview":  (task.request_preview or "")[:120],
        "result":           None,
    }

def _json_response(handler, status: int, body: dict):
    data = json.dumps(body, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)

class _StatusHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # 屏蔽默认 access log

    def do_GET(self):
        # GET /status
        if self.path == "/status":
            with _tasks_lock:
                active = [t for t in _active_conv_tasks.values() if not t.reported]
            _json_response(self, 200, {
                "active_count":   len(active),
                "max_concurrent": MAX_CONCURRENT_TASKS,
                "tasks":          [_task_to_dict(t) for t in active],
                "monitor":        _monitor_snapshot(include_events=False, include_errors=False),
            })
            return

        # GET /events
        if self.path == "/events":
            _json_response(self, 200, _monitor_snapshot(include_events=True, include_errors=False))
            return

        # GET /errors
        if self.path == "/errors":
            _json_response(self, 200, _monitor_snapshot(include_events=False, include_errors=True))
            return

        # GET /status/{msg_id}
        if self.path.startswith("/status/"):
            msg_id = self.path[len("/status/"):]
            with _tasks_lock:
                task = next(
                    (t for t in _active_conv_tasks.values()
                     if str(t.clawith_msg_id) == msg_id and not t.reported),
                    None
                )
            if task is None:
                _json_response(self, 404, {"error": "task not found"})
            else:
                _json_response(self, 200, _task_to_dict(task))
            return

        _json_response(self, 404, {"error": "not found"})

    def do_POST(self):
        import re
        m = re.match(r"^/session/([^/]+)/decide$", self.path)
        if m:
            conv_id = m.group(1)
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode()) if length else {}
            except Exception:
                _json_response(self, 400, {"error": "invalid JSON"})
                return

            decision = body.get("decision", "")
            if decision not in ("allow", "deny"):
                _json_response(self, 400, {"error": "decision must be 'allow' or 'deny'"})
                return

            with _tasks_lock:
                task = _active_conv_tasks.get(conv_id)

            if task is None or task.reported:
                _json_response(self, 404, {"error": "no active task for conv_id"})
                return

            if not task._waiting_permission:
                _json_response(self, 200, {"ok": False, "msg": "没有等待决策的任务"})
                return

            task.permission_decision = decision
            task.pending_permission.set()
            log.info(f"[decide] conv={conv_id[:8]} decision={decision}")
            _json_response(self, 200, {"ok": True, "msg": f"决策已注入: {decision}"})
            return

        _json_response(self, 404, {"error": "not found"})


def start_status_server():
    server = http.server.HTTPServer(("127.0.0.1", STATUS_PORT), _StatusHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="status-server")
    t.start()
    log.info(f"[status] HTTP server listening on 127.0.0.1:{STATUS_PORT}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not CLAWITH_API_KEY:
        log.error("CLAWITH_API_KEY is not set")
        sys.exit(1)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, 'SIGBREAK'):  # Windows
        signal.signal(signal.SIGBREAK, _signal_handler)

    log.info("Clawith Bridge-Claude starting")
    log.info(f"  Clawith:       {CLAWITH_API_URL}")
    log.info(f"  Workdir base:  {CLAUDE_WORKDIR_BASE}")
    log.info(f"  Permission:    {CLAUDE_PERMISSION_MODE}")
    log.info(f"  Max turns:     {CLAUDE_MAX_TURNS}")
    log.info(f"  HTTP timeout:  {HTTP_TIMEOUT_SECONDS}s")
    log.info(f"  HTTP retries:  {HTTP_MAX_RETRIES}")
    log.info(f"  Subprocess mgmt: psutil={'available' if psutil else 'unavailable (fallback to os.kill)'}")

    recover_inflight()

    threading.Thread(target=timeout_monitor, daemon=True, name="timeout").start()

    start_status_server()

    clawith_heartbeat()
    log.info("[startup] ready, polling Clawith...")

    heartbeat_counter = 0
    idle_poll_counter = 0
    while not _shutdown_event.is_set():
        try:
            messages = clawith_poll()
            if messages:
                idle_poll_counter = 0
                log.info(f"[poll] {len(messages)} message(s)")
                for msg in messages:
                    try:
                        process_message(msg)
                    except Exception as e:
                        log.error(f"process_message error: {e}", exc_info=True)
                        _monitor_event(
                            "process_message_exception",
                            level="error",
                            msg_id=str(msg.get("id")),
                            conv_id=str(msg.get("conversation_id") or msg.get("id")),
                            error=_short_text(str(e), 240),
                        )
                        try:
                            clawith_report(msg.get("id"), f"处理消息时出错: {e}")
                        except Exception:
                            pass
            else:
                idle_poll_counter += 1
                if IDLE_POLL_LOG_EVERY > 0 and idle_poll_counter >= IDLE_POLL_LOG_EVERY:
                    log.info(f"[poll] idle for {idle_poll_counter * POLL_INTERVAL}s")
                    idle_poll_counter = 0

            heartbeat_counter += 1
            if heartbeat_counter >= 12:
                clawith_heartbeat()
                heartbeat_counter = 0

        except KeyboardInterrupt:
            log.info("Received keyboard interrupt")
            _cleanup_all_tasks()
            break
        except Exception as e:
            log.error(f"Poll loop error: {e}", exc_info=True)
            _monitor_event("poll_loop_exception", level="error", error=_short_text(str(e), 240))

        time.sleep(POLL_INTERVAL)
    
    log.info("[main] poll loop exited")


if __name__ == "__main__":
    main()

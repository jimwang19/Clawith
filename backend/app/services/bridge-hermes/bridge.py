#!/usr/bin/env python3
"""
Clawith Bridge v2 — 使用 prompt_async + SSE 事件流

架构:
  1. 轮询 Clawith 获取新消息
  2. 用 prompt_async 异步发给 OpenCode (不阻塞)
  3. 订阅 OpenCode SSE /event 流，实时监听状态变化
  4. 状态变化时通过 Clawith send-message 推送中间状态
  5. OpenCode 完成后 report 最终结果给 Clawith
  6. 如果 OpenCode 需要权限确认，转发给 Clawith 用户，等待回复后响应

需要: Python 3.8+
"""

import json
import os
import sys
import time
import random
import logging
import threading
import urllib.request
import urllib.error
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── Config ────────────────────────────────────────────────────────────────────

CLAWITH_API_URL  = os.environ.get("CLAWITH_API_URL",  "http://127.0.0.1:8000")
CLAWITH_API_KEY  = os.environ.get("CLAWITH_API_KEY",  "")
OPENCODE_HOST    = os.environ.get("OPENCODE_HOST",    "127.0.0.1")
OPENCODE_PORT    = int(os.environ.get("OPENCODE_PORT", "4096"))
OPENCODE_WORKDIR = os.environ.get("OPENCODE_WORKDIR", "/code")
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL", "5"))
OPENCODE_TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "300"))
CLAWITH_SEND_ENABLED = os.environ.get("CLAWITH_SEND_ENABLED", "0") == "1"
IDLE_POLL_LOG_EVERY = int(os.environ.get("IDLE_POLL_LOG_EVERY", "12"))
PROGRESS_VIA_REPORT = os.environ.get("PROGRESS_VIA_REPORT", "1") == "1"
INFLIGHT_RECOVER_MAX_AGE = int(os.environ.get("INFLIGHT_RECOVER_MAX_AGE", "900"))
BRIDGE_TRACE_IO = os.environ.get("BRIDGE_TRACE_IO", "1") == "1"
TRACE_TEXT_LIMIT = int(os.environ.get("TRACE_TEXT_LIMIT", "240"))
HTTP_TIMEOUT_SECONDS  = int(os.environ.get("HTTP_TIMEOUT_SECONDS", "10"))
HTTP_MAX_RETRIES      = int(os.environ.get("HTTP_MAX_RETRIES", "2"))
HTTP_RETRY_BASE_DELAY = float(os.environ.get("HTTP_RETRY_BASE_DELAY", "0.7"))
HTTP_RETRY_MAX_DELAY  = float(os.environ.get("HTTP_RETRY_MAX_DELAY", "5"))
HTTP_RETRY_JITTER     = float(os.environ.get("HTTP_RETRY_JITTER", "0.25"))

SELF_AGENT_NAME  = os.environ.get("BRIDGE_AGENT_NAME", "hermes-agent")
MAX_CONCURRENT_TASKS = int(os.environ.get("MAX_CONCURRENT_TASKS", "2"))
STATUS_PORT      = int(os.environ.get("BRIDGE_STATUS_PORT", "8766"))
MONITOR_EVENTS_MAX = int(os.environ.get("MONITOR_EVENTS_MAX", "200"))
MONITOR_ERROR_MAX  = int(os.environ.get("MONITOR_ERROR_MAX", "100"))

OPENCODE_URL = os.environ.get("OPENCODE_URL", f"http://{OPENCODE_HOST}:{OPENCODE_PORT}")
INFLIGHT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "inflight.json")

# ── Logging ───────────────────────────────────────────────────────────────────

os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bridge")

# ── Monitor (in-memory event queue & stats) ───────────────────────────────────

_monitor_lock   = threading.Lock()
_monitor_events = deque(maxlen=MONITOR_EVENTS_MAX)
_monitor_errors = deque(maxlen=MONITOR_ERROR_MAX)
_monitor_stats  = {
    "started_at":            int(time.time()),
    "messages_received":     0,
    "tasks_started":         0,
    "tasks_succeeded":       0,
    "tasks_failed":          0,
    "busy_rejected":         0,
    "concurrency_rejected":  0,
    "permission_requests":   0,
    "permission_denied":     0,
    "permission_allowed":    0,
    "exceptions":            0,
}


def _monitor_inc(key: str, delta: int = 1):
    with _monitor_lock:
        _monitor_stats[key] = int(_monitor_stats.get(key, 0) or 0) + delta


def _monitor_event(event: str, level: str = "info", **kwargs):
    entry = {"ts": int(time.time()), "event": event, "level": level}
    entry.update({k: v for k, v in kwargs.items() if v is not None})
    with _monitor_lock:
        _monitor_events.append(entry)
        if level in {"warning", "error"}:
            _monitor_errors.append(entry)
        if level == "error":
            _monitor_stats["exceptions"] = int(_monitor_stats.get("exceptions", 0) or 0) + 1


def _monitor_snapshot(include_events: bool = False, include_errors: bool = False) -> dict:
    with _monitor_lock:
        stats  = dict(_monitor_stats)
        events = list(_monitor_events) if include_events else None
        errors = list(_monitor_errors) if include_errors else None
    body = {
        "stats":    stats,
        "uptime_s": max(0, int(time.time()) - int(stats.get("started_at", int(time.time())))),
    }
    if events is not None:
        body["events"] = events
    if errors is not None:
        body["errors"] = errors
    return body


def _short_text(text: str, limit: int = 120) -> str:
    """单行摘要，便于日志追踪。"""
    s = (text or "").replace("\n", " ").strip()
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def _trace_value(v, limit: int = TRACE_TEXT_LIMIT):
    """将日志字段压缩为可读、可控长度。"""
    if isinstance(v, str):
        return _short_text(v, limit)
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            out[k] = _trace_value(val, limit)
        return out
    if isinstance(v, list):
        if len(v) <= 8:
            return [_trace_value(x, limit) for x in v]
        head = [_trace_value(x, limit) for x in v[:8]]
        head.append(f"...(+{len(v) - 8})")
        return head
    return v


def _trace(event: str, **fields):
    """结构化追踪日志：用于跨环境比对消息流转。"""
    if not BRIDGE_TRACE_IO:
        return
    try:
        compact = {k: _trace_value(v) for k, v in fields.items()}
        log.info(f"[trace] {event} {json.dumps(compact, ensure_ascii=False, sort_keys=True)}")
    except Exception as e:
        log.debug(f"[trace] build failed for {event}: {e}")

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _http(method: str, url: str, data=None, headers=None, timeout=None):
    """HTTP 请求，对网络错误/5xx 做指数退避重试。"""
    if timeout is None:
        timeout = HTTP_TIMEOUT_SECONDS
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    body = json.dumps(data).encode() if data is not None else None

    attempt = 0
    while True:
        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            if e.code < 500 or attempt >= HTTP_MAX_RETRIES:
                log.error(f"HTTP {e.code} {method} {url}: {raw[:200]}")
                return e.code, {}
        except Exception as e:
            if attempt >= HTTP_MAX_RETRIES:
                log.error(f"Request failed {method} {url}: {e}")
                return 0, {}

        attempt += 1
        delay = min(HTTP_RETRY_BASE_DELAY * (2 ** (attempt - 1)), HTTP_RETRY_MAX_DELAY)
        delay += random.uniform(0, HTTP_RETRY_JITTER)
        log.warning(f"[http] retry {attempt}/{HTTP_MAX_RETRIES} {method} {url} in {delay:.1f}s")
        time.sleep(delay)


# ── Clawith API ───────────────────────────────────────────────────────────────

def clawith_poll():
    status, body = _http("GET", f"{CLAWITH_API_URL}/api/gateway/poll",
        headers={"X-Api-Key": CLAWITH_API_KEY})
    if status == 200:
        messages = body.get("messages", [])
        _trace(
            "clawith.poll",
            status=status,
            count=len(messages),
            ids=[str(m.get("id")) for m in messages],
            convs=[str(m.get("conversation_id") or m.get("id")) for m in messages],
            senders=[m.get("sender_user_name") or m.get("sender_agent_name") for m in messages],
        )
        return messages
    _trace("clawith.poll", status=status, count=0)
    return []

def clawith_report(message_id: str, result: str):
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
        headers={"X-Api-Key": CLAWITH_API_KEY})
    _trace(
        "clawith.report",
        message_id=str(message_id),
        uuid_id=uuid_id,
        status=status,
        result_preview=_short_text(result, 200),
    )
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
    status, _ = _http("POST", f"{CLAWITH_API_URL}/api/gateway/send-message",
        data={"conversation_id": conversation_id, "content": content},
        headers={"X-Api-Key": CLAWITH_API_KEY})
    _trace(
        "clawith.send",
        conversation_id=str(conversation_id),
        status=status,
        content_preview=_short_text(content, 200),
    )
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

# ── OpenCode API ──────────────────────────────────────────────────────────────

def opencode_health():
    status, body = _http("GET", f"{OPENCODE_URL}/global/health")
    return status == 200 and body.get("healthy", False)

def opencode_create_session(title: str = None) -> str | None:
    t = title or f"clawith-{uuid.uuid4().hex[:8]}"
    status, body = _http("POST", f"{OPENCODE_URL}/session", data={"title": t})
    if status == 200:
        return body.get("id")
    return None

def opencode_prompt_async(session_id: str, content: str, history: list = None) -> bool:
    """异步发送消息，立即返回 204，不阻塞
    注意：OpenCode session 自身维护对话历史，无需 bridge 额外传入 history"""
    full_content = content

    _trace(
        "opencode.prompt_async.request",
        session_id=session_id,
        content_preview=_short_text(full_content, 200),
        history_len=len(history or []),
    )

    status, body = _http("POST", f"{OPENCODE_URL}/session/{session_id}/prompt_async",
        data={"parts": [{"type": "text", "text": full_content}]},
        timeout=15)
    _trace(
        "opencode.prompt_async.response",
        session_id=session_id,
        status=status,
        body=body,
    )
    if status == 204:
        log.info(f"[opencode] prompt_async ok session={session_id[:8]} text='{_short_text(full_content)}'")
    else:
        log.error(f"[opencode] prompt_async failed status={status} session={session_id[:8]}")
    return status == 204

def opencode_get_messages(session_id: str) -> list:
    _, body = _http("GET", f"{OPENCODE_URL}/session/{session_id}/message")
    return body if isinstance(body, list) else []

def opencode_respond_permission(session_id: str, permission_id: str, response: str = "allow", remember: bool = False):
    """响应 OpenCode 的权限请求"""
    _http("POST", f"{OPENCODE_URL}/session/{session_id}/permissions/{permission_id}",
        data={"response": response, "remember": remember})

def extract_final_text(messages: list) -> str:
    """从消息列表中提取最后一条助手回复文本"""
    for msg in reversed(messages):
        info = msg.get("info", {}) if isinstance(msg, dict) else {}
        if info.get("role") != "assistant":
            continue
        texts = [
            p.get("text", "").strip()
            for p in msg.get("parts", [])
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        if texts:
            return "\n".join(texts)
    return "(无回复)"

# ── Task 状态跟踪 ─────────────────────────────────────────────────────────────

class TaskState:
    def __init__(
        self,
        clawith_msg_id: str,
        conv_id: str,
        opencode_session_id: str,
        request_preview: str = "",
        sender_is_agent: bool = False,
    ):
        self.clawith_msg_id      = clawith_msg_id
        self.conv_id             = conv_id
        self.opencode_session_id = opencode_session_id
        self.request_preview     = request_preview
        self.sender_is_agent     = sender_is_agent
        self.reported            = False
        self.start_time          = time.time()
        self.last_status_push    = 0.0
        self.last_progress_text  = ""
        self.pending_permissions = {}   # permission_id -> props
        self.tool_calls          = []   # 记录工具调用

# opencode_session_id -> TaskState
_active_tasks: dict[str, TaskState] = {}
# conv_id -> TaskState（同一会话只允许一个进行中任务）
_active_conv_tasks: dict[str, TaskState] = {}
_tasks_lock = threading.Lock()

# conv_id -> opencode_session_id（跨消息复用 session）
_sessions: dict[str, str] = {}

# ── 全局并发控制 ──────────────────────────────────────────────────────────────

_concurrent_count = 0
_concurrent_lock  = threading.Lock()


def try_acquire_slot(task: "TaskState") -> bool:
    global _concurrent_count
    with _concurrent_lock:
        if _concurrent_count >= MAX_CONCURRENT_TASKS:
            return False
        _concurrent_count += 1
        return True


def release_slot():
    global _concurrent_count
    with _concurrent_lock:
        if _concurrent_count > 0:
            _concurrent_count -= 1

def restore_sessions_from_opencode():
    """bridge 启动时，从 OpenCode 已有 session 中恢复 conv_id -> session_id 映射
    避免 bridge 重启后对同一 conversation 重复创建 session"""
    _, sessions = _http("GET", f"{OPENCODE_URL}/session")
    if not isinstance(sessions, list):
        return
    # session title 格式: clawith-{conv_id} (完整 conv_id; 旧 session 可能是 clawith-{conv_id[:8]})
    # 同一 conv_id 可能有多个 session，取 id 字母序最大的（最新创建）
    conv_map: dict[str, str] = {}
    conv_time: dict[str, int] = {}
    for s in sessions:
        title = s.get("title", "")
        sid = s.get("id", "")
        created = s.get("time", {}).get("created", 0)
        if title.startswith("clawith-") and sid:
            conv_id = title[len("clawith-"):]
            # 取创建时间最新的 session
            if conv_id not in conv_map or created > conv_time.get(conv_id, 0):
                conv_map[conv_id] = sid
                conv_time[conv_id] = created
    if conv_map:
        _sessions.update(conv_map)
        log.info(f"[startup] restored {len(conv_map)} session(s) from OpenCode: {list(conv_map.keys())}")

# ── 状态推送限流 ──────────────────────────────────────────────────────────────

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
            progress_text = f"⏳ {message}（正在等待结果，请勿重发）"
            clawith_report(task.clawith_msg_id, progress_text)
            log.info(
                f"[push] progress_report msg={str(task.clawith_msg_id)[:8]} "
                f"session={task.opencode_session_id[:8]} text='{_short_text(progress_text)}'"
            )
    except Exception as e:
        log.debug(f"[push] error: {e}")

# ── SSE 事件处理 ──────────────────────────────────────────────────────────────

def handle_sse_event(evt: dict):
    etype = evt.get("type", "")
    props = evt.get("properties", {})

    if etype in ("server.connected", "server.heartbeat"):
        return

    # sessionID 在顶层 properties.sessionID
    session_id = props.get("sessionID")
    if not session_id:
        return

    with _tasks_lock:
        task = _active_tasks.get(session_id)
    if not task or task.reported:
        return

    # ── session 状态事件（完成信号在这里，不是 session.updated） ──
    if etype == "session.status":
        status_obj = props.get("status", {})
        status_type = status_obj.get("type", "")
        log.info(f"  [{session_id[:8]}] session.status: {status_type}")
        if status_type == "busy":
            _push_status(task, "OpenCode 正在处理...")
        elif status_type in ("idle", "error"):
            _finalize_task(task, status_type)

    # ── 消息 part 更新（工具调用在这里） ──
    elif etype == "message.part.updated":
        part = props.get("part", {})
        if not isinstance(part, dict):
            return
        part_type = part.get("type", "")

        if part_type == "tool-invocation":
            tool_inv = part.get("toolInvocation", {})
            tool_name = tool_inv.get("toolName", "")
            tool_state = tool_inv.get("state", "")
            if tool_name and tool_state == "call":
                task.tool_calls.append(tool_name)
                log.info(f"  [{session_id[:8]}] tool: {tool_name}")
                _push_status(task, f"正在调用工具: {tool_name}...")

        elif part_type == "text":
            text = part.get("text", "").strip()
            if text:
                _push_status(task, "AI 正在回复...")

    # ── 权限请求 ──
    elif etype == "permission.requested":
        permission_id = (props.get("id")
                         or props.get("permissionID")
                         or props.get("permission", {}).get("id"))
        tool_name = props.get("toolName") or props.get("permission", {}).get("toolName", "未知工具")
        args = props.get("arguments") or props.get("permission", {}).get("arguments", {})
        if permission_id and permission_id not in task.pending_permissions:
            task.pending_permissions[permission_id] = props
            log.info(f"  [{session_id[:8]}] permission requested: {tool_name} ({permission_id[:8]})")
            args_str = json.dumps(args, ensure_ascii=False)[:200] if args else ""
            if not CLAWITH_SEND_ENABLED:
                opencode_respond_permission(task.opencode_session_id, permission_id, "deny")
                del task.pending_permissions[permission_id]
                log.warning(
                    f"[perm] auto-deny conv={task.conv_id[:8]} session={session_id[:8]} "
                    f"permission={permission_id[:8]} tool={tool_name}"
                )
                result = (
                    "⚠️ OpenCode 请求执行高风险或受限操作，但当前 bridge 已禁用中间确认通道，"
                    "本次请求已自动拒绝。\n\n"
                    f"工具: {tool_name}\n"
                    + (f"参数: {args_str}\n" if args_str else "")
                    + "请在允许人工确认的模式下重试。"
                )
                _finish_task_with_result(task, result, "permission denied without send channel")
                return
            msg = (
                f"⚠️ **OpenCode 请求权限确认**\n"
                f"工具: `{tool_name}`\n"
                + (f"参数: `{args_str}`\n" if args_str else "")
                + f"\n请回复 **允许** 或 **拒绝**\n"
                f"_(permission: `{permission_id}`）_"
            )
            _push_status(task, msg)
            # 重置限流，确保权限消息一定能发出去
            task.last_status_push = 0

def _finish_task_with_result(task: TaskState, result: str, reason: str):
    """直接结束任务并回传指定结果。"""
    if task.reported:
        return

    task.reported = True
    with _tasks_lock:
        _active_tasks.pop(task.opencode_session_id, None)
        if _active_conv_tasks.get(task.conv_id) is task:
            _active_conv_tasks.pop(task.conv_id, None)
    release_slot()

    elapsed = int(time.time() - task.start_time)
    is_failure = reason in ("permission denied without send channel",) or result.startswith("❌")
    if is_failure:
        _monitor_inc("tasks_failed")
        _monitor_event("task_failed", level="error",
                       msg_id=str(task.clawith_msg_id), conv_id=str(task.conv_id),
                       elapsed_s=elapsed, reason=reason)
    else:
        _monitor_inc("tasks_succeeded")
        _monitor_event("task_finished", level="info",
                       msg_id=str(task.clawith_msg_id), conv_id=str(task.conv_id),
                       elapsed_s=elapsed)

    log.info(f"[done] {task.opencode_session_id[:8]} → Clawith ({reason})")
    _trace(
        "bridge.finish_with_result",
        reason=reason,
        msg_id=str(task.clawith_msg_id),
        conv_id=str(task.conv_id),
        session_id=str(task.opencode_session_id),
        result_preview=_short_text(result, 200),
    )
    inflight_remove(str(task.clawith_msg_id))
    clawith_report(task.clawith_msg_id, result)

def _finalize_task(task: TaskState, status: str):
    """完成任务，获取最终结果并 report 给 Clawith"""
    if task.reported:
        return
    task.reported = True

    with _tasks_lock:
        _active_tasks.pop(task.opencode_session_id, None)
        if _active_conv_tasks.get(task.conv_id) is task:
            _active_conv_tasks.pop(task.conv_id, None)
    release_slot()

    messages = opencode_get_messages(task.opencode_session_id)
    result = extract_final_text(messages)

    if status == "error":
        result = f"❌ OpenCode 处理出错\n\n{result}"
    elif task.tool_calls:
        tools = ", ".join(dict.fromkeys(task.tool_calls))
        result = f"*(调用了: {tools})*\n\n{result}"

    elapsed = int(time.time() - task.start_time)
    log.info(f"[done] {task.opencode_session_id[:8]} ({elapsed}s) → Clawith")
    if status == "error":
        _monitor_inc("tasks_failed")
        _monitor_event("task_failed", level="error",
                       msg_id=str(task.clawith_msg_id), conv_id=str(task.conv_id),
                       elapsed_s=elapsed, reason="opencode error")
    else:
        _monitor_inc("tasks_succeeded")
        _monitor_event("task_finished", level="info",
                       msg_id=str(task.clawith_msg_id), conv_id=str(task.conv_id),
                       elapsed_s=elapsed, tools=list(dict.fromkeys(task.tool_calls)))
    _trace(
        "bridge.finalize",
        status=status,
        elapsed_s=elapsed,
        msg_id=str(task.clawith_msg_id),
        conv_id=str(task.conv_id),
        session_id=str(task.opencode_session_id),
        tools=list(dict.fromkeys(task.tool_calls)),
        result_preview=_short_text(result, 200),
    )
    inflight_remove(str(task.clawith_msg_id))
    clawith_report(task.clawith_msg_id, result)

# ── SSE 监听线程 ──────────────────────────────────────────────────────────────

_sse_running = False

def sse_listener():
    global _sse_running
    _sse_running = True
    log.info(f"[sse] connecting to {OPENCODE_URL}/event")

    while _sse_running:
        try:
            req = urllib.request.Request(f"{OPENCODE_URL}/event")
            with urllib.request.urlopen(req, timeout=None) as resp:
                log.info("[sse] connected")
                buf = ""
                while _sse_running:
                    chunk = resp.read(512).decode("utf-8", errors="ignore")
                    if not chunk:
                        break
                    buf += chunk
                    while "\n\n" in buf:
                        event_str, buf = buf.split("\n\n", 1)
                        for line in event_str.splitlines():
                            if line.startswith("data:"):
                                try:
                                    evt = json.loads(line[5:].strip())
                                    handle_sse_event(evt)
                                except Exception as e:
                                    log.debug(f"[sse] parse error: {e}")
        except Exception as e:
            if _sse_running:
                log.warning(f"[sse] disconnected: {e}, reconnect in 3s...")
                time.sleep(3)
    log.info("[sse] stopped")

# ── 超时监控线程 ──────────────────────────────────────────────────────────────

def _check_session_and_report(task: TaskState, reason: str):
    """兜底检查 OpenCode session，如果已稳定产出助手文本则 report"""
    if task.reported:
        return
    
    # OpenCode GET /session/{id} 返回扁平对象，不包含 info.status
    status_code, status_body = _http("GET", f"{OPENCODE_URL}/session/{task.opencode_session_id}")
    if status_code != 200:
        log.debug(f"[check] session status API failed: {status_code}")
        return
    
    updated_ms = status_body.get("time", {}).get("updated", 0) if isinstance(status_body, dict) else 0
    
    # 获取最新消息
    messages = opencode_get_messages(task.opencode_session_id)
    final_text = extract_final_text(messages)
    
    if not final_text or final_text == "(无回复)":
        log.debug(f"[check] {task.opencode_session_id[:8]} still processing, no text yet")
        return

    # 定时轮询时，如果 session 最近仍在更新，先不抢在 SSE 之前回传
    now_ms = int(time.time() * 1000)
    if reason == "定时轮询" and updated_ms and now_ms - updated_ms < 15000:
        log.debug(
            f"[check] {task.opencode_session_id[:8]} has assistant text but was updated recently, wait more"
        )
        return

    log.info(f"[check] {task.opencode_session_id[:8]} has assistant text, reporting due to {reason}")
    result_text = final_text if reason == "定时轮询" else f"(检查发现：{reason})\n\n{final_text}"
    _trace(
        "bridge.check_report",
        reason=reason,
        msg_id=str(task.clawith_msg_id),
        conv_id=str(task.conv_id),
        session_id=str(task.opencode_session_id),
        result_preview=_short_text(result_text, 200),
    )
    
    task.reported = True
    with _tasks_lock:
        _active_tasks.pop(task.opencode_session_id, None)
        if _active_conv_tasks.get(task.conv_id) is task:
            _active_conv_tasks.pop(task.conv_id, None)
    release_slot()
    elapsed = int(time.time() - task.start_time)
    _monitor_inc("tasks_succeeded")
    _monitor_event("task_finished", level="info",
                   msg_id=str(task.clawith_msg_id), conv_id=str(task.conv_id),
                   elapsed_s=elapsed, via=reason)
    clawith_report(task.clawith_msg_id, result_text)
    log.info(f"[done] {task.opencode_session_id[:8]} -> Clawith (via {reason})")

def timeout_monitor():
    """后台监控线程：
    1. 超时检查（每 10 秒）：检查超过 OPENCODE_TIMEOUT 的任务
    2. 状态轮询（每 60 秒）：检查所有活跃 session 是否已完成但未触发 SSE
    """
    last_check = 0
    while True:
        time.sleep(10)
        now = time.time()
        
        # 检查超时任务
        with _tasks_lock:
            timed_out = [
                t for t in _active_tasks.values()
                if not t.reported and now - t.start_time > OPENCODE_TIMEOUT
            ]
        for task in timed_out:
            log.warning(f"[timeout] {task.clawith_msg_id[:8]} after {OPENCODE_TIMEOUT}s")
            _check_session_and_report(task, "超时检查")
        
        # 每 60 秒做一次状态轮询（兜底机制：防止 SSE 事件丢失）
        if now - last_check >= 60:
            last_check = now
            with _tasks_lock:
                active = [t for t in _active_tasks.values() if not t.reported]
            if active:
                log.debug(f"[check] polling {len(active)} active session(s)...")
                for task in active:
                    _check_session_and_report(task, "定时轮询")

def _handle_permission_reply(content: str, conv_id: str) -> bool:
    """检查用户是否在回复权限请求"""
    c = content.strip()
    allow = c in ("允许", "allow", "yes", "y", "ok", "确认", "同意")
    deny  = c in ("拒绝", "deny", "no", "n", "不", "否", "不允许")

    if not allow and not deny:
        return False

    with _tasks_lock:
        tasks_snapshot = [t for t in _active_tasks.values() if t.conv_id == conv_id]

    for task in tasks_snapshot:
        for perm_id in list(task.pending_permissions.keys()):
            response = "allow" if allow else "deny"
            opencode_respond_permission(task.opencode_session_id, perm_id, response)
            del task.pending_permissions[perm_id]
            log.info(
                f"[perm] response={response} conv={conv_id[:8]} "
                f"session={task.opencode_session_id[:8]} permission={perm_id[:8]}"
            )
            return True

    return False

# ── 消息处理 ──────────────────────────────────────────────────────────────────

def process_message(msg: dict):
    msg_id  = msg.get("id")
    content = msg.get("content", "")
    conv_id = msg.get("conversation_id") or msg_id
    history = msg.get("history", [])
    sender  = msg.get("sender_user_name") or msg.get("sender_agent_name") or "user"

    # 自回声过滤：丢弃 bridge 自己发出的消息（避免 busy 拒绝消息导致循环）
    sender_agent = (msg.get("sender_agent_name") or "").strip()
    if sender_agent.lower() == SELF_AGENT_NAME.lower():
        log.debug(f"[msg] drop self-echo id={str(msg_id)[:8]} sender='{sender_agent}'")
        return

    log.info(
        f"[msg] recv id={str(msg_id)[:8]} conv={str(conv_id)[:8]} "
        f"sender='{sender}' text='{_short_text(content)}'"
    )
    _monitor_inc("messages_received")
    _monitor_event(
        "message_received",
        level="info",
        msg_id=str(msg_id),
        conv_id=str(conv_id),
        sender=sender,
        text=_short_text(content, 180),
    )
    _trace(
        "bridge.inbound",
        msg_id=str(msg_id),
        conv_id=str(conv_id),
        sender=sender,
        sender_user_name=msg.get("sender_user_name"),
        sender_agent_name=msg.get("sender_agent_name"),
        content_preview=_short_text(content, 220),
        history_len=len(history or []),
    )

    # 检查是否是权限回复
    if _handle_permission_reply(content, conv_id):
        _trace(
            "bridge.permission_reply",
            msg_id=str(msg_id),
            conv_id=str(conv_id),
            content_preview=_short_text(content, 120),
        )
        clawith_report(msg_id, "✅ 已处理权限请求")
        return

    # 同一会话只允许一个进行中任务：不排队，不新开 session
    with _tasks_lock:
        running_task = _active_conv_tasks.get(conv_id)
        if running_task and not running_task.reported:
            elapsed = int(time.time() - running_task.start_time)
            preview = (running_task.request_preview or "(上一个任务)").replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:80] + "..."
            clawith_report(
                msg_id,
                f"⏳ 前面有任务还在处理（已进行 {elapsed}s）\n"
                f"当前任务: {preview}\n"
                "请等待该任务完成后再发送新消息。"
            )
            log.info(
                f"[msg] rejected_busy id={str(msg_id)[:8]} conv={str(conv_id)[:8]} "
                f"active_session={running_task.opencode_session_id[:8]} elapsed={elapsed}s"
            )
            _monitor_inc("busy_rejected")
            _monitor_event(
                "busy_reject",
                level="warning",
                msg_id=str(msg_id),
                conv_id=str(conv_id),
                elapsed_s=elapsed,
            )
            _trace(
                "bridge.rejected_busy",
                msg_id=str(msg_id),
                conv_id=str(conv_id),
                active_session=str(running_task.opencode_session_id),
                elapsed_s=elapsed,
                active_preview=preview,
            )
            return

    # 全局并发控制（超限直接拒绝，返回 429）
    task_placeholder = type("_T", (), {"conv_id": conv_id})()
    if not try_acquire_slot(task_placeholder):
        clawith_report(
            msg_id,
            f"429 并发任务已达上限（最多 {MAX_CONCURRENT_TASKS} 个），请稍后重试。"
        )
        log.warning(
            f"[msg] concurrency_rejected id={str(msg_id)[:8]} conv={str(conv_id)[:8]} "
            f"concurrent={_concurrent_count}/{MAX_CONCURRENT_TASKS}"
        )
        _monitor_inc("concurrency_rejected")
        _monitor_event(
            "concurrency_reject",
            level="warning",
            msg_id=str(msg_id),
            conv_id=str(conv_id),
            max_concurrent=MAX_CONCURRENT_TASKS,
        )
        return

    # 获取或创建 OpenCode session
    session_id = _sessions.get(conv_id)
    if not session_id:
        session_id = opencode_create_session(f"clawith-{conv_id}")
        if not session_id:
            log.error("Failed to create OpenCode session")
            release_slot()
            _monitor_event("session_create_failed", level="error",
                           msg_id=str(msg_id), conv_id=str(conv_id))
            clawith_report(msg_id, "❌ 无法创建 OpenCode 会话，请检查 opencode serve 是否运行")
            return
        _sessions[conv_id] = session_id
        log.info(f"[session] created conv={str(conv_id)[:8]} -> session={session_id[:8]}")
        _trace("bridge.session", action="created", conv_id=str(conv_id), session_id=str(session_id))
    else:
        log.info(f"[session] reuse conv={str(conv_id)[:8]} -> session={session_id[:8]}")
        _trace("bridge.session", action="reuse", conv_id=str(conv_id), session_id=str(session_id))

    # 注册 task
    task = TaskState(
        msg_id,
        conv_id,
        session_id,
        request_preview=content,
        sender_is_agent=bool(msg.get("sender_agent_name")),
    )
    with _tasks_lock:
        _active_tasks[session_id] = task
        _active_conv_tasks[conv_id] = task
    inflight_add(str(msg_id), content, str(conv_id))

    log.info(
        f"[route] msg={str(msg_id)[:8]} conv={str(conv_id)[:8]} "
        f"-> session={session_id[:8]}"
    )
    _monitor_inc("tasks_started")
    _monitor_event(
        "task_started",
        level="info",
        msg_id=str(msg_id),
        conv_id=str(conv_id),
        session_id=str(session_id),
        text=_short_text(content, 180),
    )
    _trace(
        "bridge.route",
        msg_id=str(msg_id),
        conv_id=str(conv_id),
        session_id=str(session_id),
    )

    # 异步发送给 OpenCode（直接发送原始内容，不加前缀）
    full_content = content
    ok = opencode_prompt_async(session_id, full_content, history)
    if not ok:
        with _tasks_lock:
            _active_tasks.pop(session_id, None)
            if _active_conv_tasks.get(conv_id) is task:
                _active_conv_tasks.pop(conv_id, None)
        release_slot()
        log.error("Failed to send async prompt")
        _trace(
            "bridge.route_failed",
            msg_id=str(msg_id),
            conv_id=str(conv_id),
            session_id=str(session_id),
        )
        clawith_report(msg_id, "❌ 发送消息到 OpenCode 失败，请重试")
        return

    log.info(f"[msg] queued → session {session_id[:8]}, waiting for SSE...")

# ── Status HTTP Server ─────────────────────────────────────────────────────────


def _task_to_dict(task: TaskState) -> dict:
    return {
        "msg_id":          str(task.clawith_msg_id),
        "conv_id":         str(task.conv_id),
        "session_id":      str(task.opencode_session_id),
        "status":          "running" if not task.reported else "done",
        "elapsed_s":       int(time.time() - task.start_time),
        "tool_calls":      list(task.tool_calls),
        "last_progress":   task.last_progress_text,
        "request_preview": (task.request_preview or "")[:120],
    }


class _StatusHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def _send_json(self, code: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/status":
            with _tasks_lock:
                tasks = [_task_to_dict(t) for t in _active_tasks.values() if not t.reported]
            self._send_json(200, {
                "active_count":   len(tasks),
                "max_concurrent": MAX_CONCURRENT_TASKS,
                "tasks":          tasks,
                "monitor":        _monitor_snapshot(include_events=False, include_errors=False),
            })
        elif path == "/events":
            self._send_json(200, _monitor_snapshot(include_events=True, include_errors=False))
        elif path == "/errors":
            self._send_json(200, _monitor_snapshot(include_events=False, include_errors=True))
        elif path.startswith("/status/"):
            msg_id = path[len("/status/"):]
            with _tasks_lock:
                found = next(
                    (t for t in _active_tasks.values()
                     if str(t.clawith_msg_id) == msg_id and not t.reported),
                    None,
                )
            if found:
                self._send_json(200, _task_to_dict(found))
            else:
                self._send_json(404, {"error": "not found"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        parts = path.strip("/").split("/")

        # POST /session/{conv_id}/decide  → inject allow/deny
        if len(parts) == 3 and parts[0] == "session" and parts[2] == "decide":
            conv_id = parts[1]
            body = self._read_body()
            decision = body.get("decision", "")
            if decision not in ("allow", "deny"):
                self._send_json(400, {"error": "decision must be 'allow' or 'deny'"})
                return

            with _tasks_lock:
                tasks_for_conv = [
                    t for t in _active_tasks.values()
                    if str(t.conv_id) == conv_id and not t.reported
                ]
            handled = 0
            for task in tasks_for_conv:
                for perm_id in list(task.pending_permissions.keys()):
                    opencode_respond_permission(task.opencode_session_id, perm_id, decision)
                    del task.pending_permissions[perm_id]
                    handled += 1
            self._send_json(200, {"ok": True, "handled": handled, "conv_id": conv_id})

        # POST /session/{conv_id}/new-session  → clear session cache for conv
        elif len(parts) == 3 and parts[0] == "session" and parts[2] == "new-session":
            conv_id = parts[1]
            old_session = _sessions.pop(conv_id, None)
            log.info(f"[new-session] conv={conv_id[:8]} cleared session={old_session and old_session[:8]}")
            self._send_json(200, {
                "ok": True,
                "conv_id": conv_id,
                "cleared_session": old_session,
            })

        else:
            self._send_json(404, {"error": "not found"})


def start_status_server():
    server = HTTPServer(("127.0.0.1", STATUS_PORT), _StatusHandler)
    t = threading.Thread(target=server.serve_forever, name="status-http", daemon=True)
    t.start()
    log.info(f"[status] HTTP server listening on 127.0.0.1:{STATUS_PORT}")




def wait_for_opencode(max_wait=120):
    log.info(f"[startup] waiting for OpenCode at {OPENCODE_URL}...")
    for i in range(max_wait):
        if opencode_health():
            log.info(f"[startup] OpenCode ready ({i}s)")
            return True
        time.sleep(1)
        if i % 10 == 9:
            log.info(f"[startup]   {i+1}s elapsed...")
    return False

def main():
    if not CLAWITH_API_KEY:
        log.error("CLAWITH_API_KEY is not set")
        sys.exit(1)

    log.info("Clawith Bridge v2 starting")
    log.info(f"  Clawith:  {CLAWITH_API_URL}")
    log.info(f"  OpenCode: {OPENCODE_URL}")
    log.info(f"  MAX_CONCURRENT_TASKS={MAX_CONCURRENT_TASKS}")
    log.info(f"  BRIDGE_STATUS_PORT={STATUS_PORT}")

    if not wait_for_opencode():
        log.error("OpenCode server did not start in time")
        sys.exit(1)

    # 恢复上次崩溃时未完成的 in-flight 任务
    recover_inflight()

    # 从 OpenCode 恢复已有 session 缓存
    restore_sessions_from_opencode()

    # 启动 Status HTTP server
    start_status_server()

    # 启动 SSE 监听线程
    threading.Thread(target=sse_listener, daemon=True, name="sse").start()

    # 启动超时监控线程
    threading.Thread(target=timeout_monitor, daemon=True, name="timeout").start()

    # 初始心跳
    clawith_heartbeat()
    log.info("[startup] ready, polling Clawith...")

    heartbeat_counter = 0
    idle_poll_counter = 0
    while True:
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
            log.info("Shutting down")
            global _sse_running
            _sse_running = False
            break
        except Exception as e:
            log.error(f"Poll loop error: {e}", exc_info=True)

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

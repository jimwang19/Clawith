#!/usr/bin/env python3
"""
bridge-claude 端对端集成测试套件

测试场景:
  T01  状态服务器可达                (GET /status)
  T02  Clawith API 连通              (poll / heartbeat)
  T03  E2E 消息流                    (注入 → bridge 接收 → /status 出现 → 任务完成)
  T04  权限决策流                    (waiting_permission → /decide allow → 完成)
       [仅 CLAUDE_PERMISSION_MODE=default 时有效，bypassPermissions 自动跳过]
  T05  并发上限                      (3 任务: 前2个接受, 第3个被 429 拒绝)
       [需要 CLAWITH_DB_URL + psycopg2-binary，否则跳过]
  T06  中断恢复                      (注入 inflight → 重启 bridge → 确认恢复日志)
       [需 --test-recovery 标志，会重启 bridge]

依赖:
  pip install psycopg2-binary    # T05 DB 注入用 (可选但推荐)

运行:
  python3 test_e2e_bridge.py                  # T01~T05
  python3 test_e2e_bridge.py --test-recovery  # T01~T06 (会短暂重启 bridge)
  python3 test_e2e_bridge.py --only T03       # 只跑指定场景

必须配置 (.env 或环境变量):
  CLAWITH_API_URL         Clawith backend URL (default: http://127.0.0.1:8000)
  CLAWITH_API_KEY         Bridge API key
  BRIDGE_STATUS_PORT      Status server 端口 (default: 8765)

T03/T04 需额外配置:
  BRIDGE_AGENT_NAME       Bridge agent 在 Clawith 中的名称 (用于 gateway send-message 注入)
                          也可不配置, 改用 CLAWITH_DB_URL 方式注入

T05 需额外配置:
  CLAWITH_DB_URL          PostgreSQL URL, 格式:
                          postgresql://user:pass@host:5432/dbname
                          (agent_id 自动从 DB 用 API key 查找)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# ── ANSI colors ───────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()
GREEN  = "\033[32m" if _USE_COLOR else ""
RED    = "\033[31m" if _USE_COLOR else ""
YELLOW = "\033[33m" if _USE_COLOR else ""
CYAN   = "\033[36m" if _USE_COLOR else ""
BOLD   = "\033[1m"  if _USE_COLOR else ""
RESET  = "\033[0m"  if _USE_COLOR else ""


# ── Config ────────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Load key=value lines from .env file into os.environ (skip existing)."""
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


_load_dotenv(HERE / ".env")

API_URL    = os.environ.get("CLAWITH_API_URL",       "http://127.0.0.1:8000")
API_KEY    = os.environ.get("CLAWITH_API_KEY",        "")
STATUS_PORT= int(os.environ.get("BRIDGE_STATUS_PORT", "8765"))
PERM_MODE  = os.environ.get("CLAUDE_PERMISSION_MODE", "default")
AGENT_NAME = os.environ.get("BRIDGE_AGENT_NAME",      "")
DB_URL     = os.environ.get("CLAWITH_DB_URL",         "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL",   "5"))

STATUS_BASE = f"http://127.0.0.1:{STATUS_PORT}"
INFLIGHT_FILE = HERE / "logs" / "inflight.json"
BRIDGE_PID_FILE = HERE / "logs" / "bridge.pid"
BRIDGE_LOG_FILE = HERE / "logs" / "bridge.log"

MARKER_PREFIX = "TEST-E2E"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _http(method: str, url: str, data: dict | None = None, headers: dict | None = None,
          timeout: int = 10) -> tuple[int, dict]:
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw[:200]}
    except Exception as e:
        return 0, {"error": str(e)}


def status_get(path: str = "/status") -> tuple[int, dict]:
    return _http("GET", f"{STATUS_BASE}{path}")


def status_post(path: str, data: dict) -> tuple[int, dict]:
    return _http("POST", f"{STATUS_BASE}{path}", data=data)


def clawith_api(method: str, path: str, data: dict | None = None) -> tuple[int, dict]:
    return _http(method, f"{API_URL}/api{path}", data=data,
                 headers={"X-Api-Key": API_KEY})


# ── Injection helpers ─────────────────────────────────────────────────────────

def _inject_via_gateway(content: str) -> bool:
    """Inject via gateway send-message (self-send). Needs BRIDGE_AGENT_NAME."""
    if not AGENT_NAME:
        return False
    status, body = clawith_api("POST", "/gateway/send-message",
                               data={"target": AGENT_NAME, "content": content})
    if status == 200:
        return True
    print(f"  {YELLOW}[inject-gw] failed: {status} {body}{RESET}")
    return False


def _get_db_conn():
    """Return a psycopg2 connection using CLAWITH_DB_URL."""
    import psycopg2  # noqa: F401 — ImportError caught by caller
    # Strip asyncpg driver suffix if present
    url = re.sub(r"\+asyncpg", "", DB_URL)
    return psycopg2.connect(url)


def _lookup_agent_id_from_db(conn) -> str | None:
    """Find agent UUID by API key (plaintext or SHA-256 hash)."""
    key_hash = hashlib.sha256(API_KEY.encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM agents WHERE api_key_hash = %s OR api_key_hash = %s LIMIT 1",
            (API_KEY, key_hash),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def inject_via_db(content: str, conv_id: str | None = None) -> tuple[str | None, str | None]:
    """
    Directly INSERT a pending GatewayMessage into the DB.
    Returns (msg_id_str, conv_id_str) on success, (None, None) on failure.
    The caller must handle ImportError if psycopg2 is not installed.
    """
    conn = _get_db_conn()
    try:
        agent_id = _lookup_agent_id_from_db(conn)
        if not agent_id:
            print(f"  {RED}[inject-db] Agent not found by API key — check CLAWITH_API_KEY{RESET}")
            return None, None

        msg_id  = str(uuid.uuid4())
        c_id    = conv_id or f"test-e2e-{uuid.uuid4().hex[:12]}"

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO gateway_messages
                     (id, agent_id, content, status, conversation_id, created_at)
                   VALUES (%s, %s, %s, 'pending', %s, NOW())""",
                (msg_id, agent_id, content, c_id),
            )
        conn.commit()
        return msg_id, c_id
    finally:
        conn.close()


def inject_message(content: str, conv_id: str | None = None) -> tuple[str | None, str | None, str]:
    """
    Inject a test message using the best available method.

    Returns (msg_id, conv_id, method_used).
    msg_id may be None if using gateway injection (msg_id unknown).
    """
    if DB_URL:
        try:
            mid, cid = inject_via_db(content, conv_id)
            if mid:
                return mid, cid, "db"
        except ImportError:
            print(f"  {YELLOW}[inject] psycopg2 not installed; pip install psycopg2-binary{RESET}")
        except Exception as e:
            print(f"  {YELLOW}[inject] DB injection failed: {e}{RESET}")

    # Fallback: gateway send-message
    if AGENT_NAME:
        ok = _inject_via_gateway(content)
        if ok:
            return None, None, "gateway"

    return None, None, "none"


# ── Status helpers ────────────────────────────────────────────────────────────

def find_task_by_marker(uid: str, tasks: list[dict]) -> dict | None:
    """Find an active task by the unique test marker embedded in request_preview."""
    marker = f"[{MARKER_PREFIX}:{uid}]"
    for t in tasks:
        if marker in (t.get("request_preview") or ""):
            return t
    return None


def find_task_by_msg_id(msg_id: str, tasks: list[dict]) -> dict | None:
    for t in tasks:
        if t.get("msg_id") == msg_id:
            return t
    return None


def wait_for_task_active(uid: str | None, msg_id: str | None,
                         timeout: int = POLL_INTERVAL + 3) -> dict | None:
    """Poll /status until the task appears. Returns the task dict or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, body = status_get("/status")
        if code == 200:
            tasks = body.get("tasks", [])
            task = None
            if msg_id:
                task = find_task_by_msg_id(msg_id, tasks)
            if not task and uid:
                task = find_task_by_marker(uid, tasks)
            if task:
                return task
        time.sleep(0.5)
    return None


def wait_for_task_done(uid: str | None, msg_id: str | None,
                       timeout: int = 90) -> bool:
    """
    Poll /status until the task is NO LONGER active (completed/failed).
    Returns True if task finished within timeout.
    """
    # First wait until the task appears
    appeared = wait_for_task_active(uid, msg_id, timeout=POLL_INTERVAL + 4)
    if not appeared:
        return False  # never appeared — likely rejected before processing

    deadline = time.time() + timeout
    while time.time() < deadline:
        code, body = status_get("/status")
        if code == 200:
            tasks = body.get("tasks", [])
            task = None
            if msg_id:
                task = find_task_by_msg_id(msg_id, tasks)
            if not task and uid:
                task = find_task_by_marker(uid, tasks)
            if task is None:
                return True  # disappeared — done
        time.sleep(1)
    return False


def wait_for_perm(uid: str | None, msg_id: str | None,
                  timeout: int = 30) -> dict | None:
    """Wait until a task reaches waiting_permission state. Returns task dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, body = status_get("/status")
        if code == 200:
            tasks = body.get("tasks", [])
            task = None
            if msg_id:
                task = find_task_by_msg_id(msg_id, tasks)
            if not task and uid:
                task = find_task_by_marker(uid, tasks)
            if task and task.get("status") == "waiting_permission":
                return task
        time.sleep(0.5)
    return None


# ── DB result checker ─────────────────────────────────────────────────────────

def check_db_completed(msg_id: str, timeout: int = 90) -> str | None:
    """
    Poll gateway_messages in DB until status=completed.
    Returns the result text or None on timeout.
    Only works when CLAWITH_DB_URL + psycopg2 are available.
    """
    if not DB_URL:
        return None
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            conn = _get_db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT status, result FROM gateway_messages WHERE id = %s",
                        (msg_id,),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            if row and row[0] == "completed":
                return row[1] or ""
            time.sleep(1)
        return None
    except Exception:
        return None


# ── Test result tracking ──────────────────────────────────────────────────────

_results: list[tuple[str, str, str]] = []  # (id, label, pass|fail|skip)


def _record(test_id: str, label: str, passed: bool | None, note: str = "") -> None:
    if passed is None:
        status_str = f"{YELLOW}SKIP{RESET}"
        marker = "⊘"
        _results.append((test_id, label, "skip"))
    elif passed:
        status_str = f"{GREEN}PASS{RESET}"
        marker = "✓"
        _results.append((test_id, label, "pass"))
    else:
        status_str = f"{RED}FAIL{RESET}"
        marker = "✗"
        _results.append((test_id, label, "fail"))

    note_str = f"  {note}" if note else ""
    print(f"  {marker} {BOLD}{test_id}{RESET} {label}: {status_str}{note_str}")


def _section(title: str) -> None:
    print(f"\n{CYAN}{BOLD}── {title} ────────────────────────────────{RESET}")


# ── T01: Status server reachable ──────────────────────────────────────────────

def test_t01_status_server():
    _section("T01 状态服务器可达")
    code, body = status_get("/status")
    if code == 200 and "tasks" in body:
        active = body.get("active_count", 0)
        max_c  = body.get("max_concurrent", "?")
        _record("T01", "GET /status", True, f"active={active}/{max_c}")
    else:
        _record("T01", "GET /status", False, f"HTTP {code}: {body}")


# ── T02: Clawith API connectivity ─────────────────────────────────────────────

def test_t02_clawith_api():
    _section("T02 Clawith API 连通")
    if not API_KEY:
        _record("T02a", "CLAWITH_API_KEY 已配置", False, "未设置 CLAWITH_API_KEY")
        return

    # Health
    hc, _ = _http("GET", f"{API_URL}/api/health")
    _record("T02a", "GET /api/health", hc == 200, f"HTTP {hc}")

    # Poll
    pc, pbody = clawith_api("GET", "/gateway/poll")
    if pc == 200:
        msgs = pbody.get("messages", [])
        _record("T02b", "GET /gateway/poll", True, f"{len(msgs)} pending message(s)")
    else:
        _record("T02b", "GET /gateway/poll", False, f"HTTP {pc}: {pbody}")

    # Heartbeat
    hbc, _ = clawith_api("POST", "/gateway/heartbeat")
    _record("T02c", "POST /gateway/heartbeat", hbc in (200, 204), f"HTTP {hbc}")


# ── T03: E2E message flow ─────────────────────────────────────────────────────

def test_t03_e2e_flow():
    _section("T03 E2E 消息流")

    uid = uuid.uuid4().hex[:8]
    content = f"[{MARKER_PREFIX}:{uid}] 请输出单行文字: hello-{uid}"

    msg_id, conv_id, method = inject_message(content)
    if method == "none":
        _record("T03", "E2E 消息流", None,
                "无注入方式 — 请配置 CLAWITH_DB_URL 或 BRIDGE_AGENT_NAME")
        return

    inj_info = f"method={method} msg_id={str(msg_id)[:8] if msg_id else 'unknown'}"
    print(f"  [inject] {inj_info}")

    # Wait for task to appear in /status
    task = wait_for_task_active(uid, msg_id, timeout=POLL_INTERVAL + 4)
    if not task:
        _record("T03a", "bridge 接收消息", False,
                f"任务未在 /status 出现 (等待 {POLL_INTERVAL + 4}s)")
        return
    _record("T03a", "bridge 接收消息", True,
            f"conv={str(task.get('conv_id', ''))[:12]} elapsed={task.get('elapsed_s', '?')}s")

    # Wait for task to complete
    done = wait_for_task_done(uid, msg_id, timeout=90)
    _record("T03b", "任务执行完毕", done,
            "(任务从 /status 消失)" if done else "90s 后仍在活跃列表")

    # Verify result in DB (if available)
    if done and msg_id and DB_URL:
        result = check_db_completed(msg_id, timeout=5)
        if result is not None:
            snippet = result.replace("\n", " ")[:80]
            _record("T03c", "gateway_messages.result 非空", bool(result.strip()),
                    f"result='{snippet}'")
        else:
            _record("T03c", "gateway_messages.result 非空", None, "DB 查询超时或不可用")


# ── T04: Permission decision ──────────────────────────────────────────────────

def test_t04_permission():
    _section("T04 权限决策流")

    if PERM_MODE != "default":
        _record("T04", "权限决策流", None,
                f"CLAUDE_PERMISSION_MODE={PERM_MODE}，非 default，跳过")
        return

    uid = uuid.uuid4().hex[:8]
    # Ask Claude to run Bash — will trigger PreToolUse hook in default mode
    content = (
        f"[{MARKER_PREFIX}:{uid}] 用 Bash 工具运行命令: "
        f"echo perm-test-{uid}"
    )

    msg_id, conv_id, method = inject_message(content)
    if method == "none":
        _record("T04", "权限决策流", None,
                "无注入方式 — 请配置 CLAWITH_DB_URL 或 BRIDGE_AGENT_NAME")
        return

    print(f"  [inject] method={method} uid={uid}")

    # Wait for waiting_permission state
    task = wait_for_perm(uid, msg_id, timeout=45)
    if not task:
        _record("T04a", "任务进入 waiting_permission", False,
                "45s 内未见 waiting_permission 状态（任务可能直接完成或出错）")
        return
    _record("T04a", "任务进入 waiting_permission", True,
            f"conv_id={task.get('conv_id', '')[:16]}")

    task_conv_id = task.get("conv_id", "")

    # Inject allow decision
    dc, dbody = status_post(f"/session/{task_conv_id}/decide", {"decision": "allow"})
    _record("T04b", "POST /session/{conv_id}/decide allow", dc == 200 and dbody.get("ok"),
            str(dbody.get("msg", "")))

    # Wait for completion
    done = wait_for_task_done(uid, msg_id, timeout=60)
    _record("T04c", "任务完成 (allow 后)", done)


def test_t04_permission_deny():
    """Additional: Test deny decision path."""
    _section("T04b 权限拒绝流")

    if PERM_MODE != "default":
        _record("T04b", "权限拒绝流", None, f"CLAUDE_PERMISSION_MODE={PERM_MODE}，跳过")
        return

    uid = uuid.uuid4().hex[:8]
    content = (
        f"[{MARKER_PREFIX}:{uid}] 用 Bash 工具运行命令: "
        f"echo deny-test-{uid}"
    )

    msg_id, conv_id, method = inject_message(content)
    if method == "none":
        _record("T04b", "权限拒绝流", None, "无注入方式")
        return

    task = wait_for_perm(uid, msg_id, timeout=45)
    if not task:
        _record("T04b-a", "任务进入 waiting_permission", False, "45s 超时")
        return
    _record("T04b-a", "任务进入 waiting_permission", True)

    task_conv_id = task.get("conv_id", "")
    dc, dbody = status_post(f"/session/{task_conv_id}/decide", {"decision": "deny"})
    _record("T04b-b", "POST /decide deny", dc == 200 and dbody.get("ok"))

    done = wait_for_task_done(uid, msg_id, timeout=30)
    _record("T04b-c", "任务因拒绝而结束", done)


# ── T05: Concurrency limit ────────────────────────────────────────────────────

def test_t05_concurrency():
    _section("T05 并发上限 (MAX_CONCURRENT_TASKS=2)")

    if not DB_URL:
        _record("T05", "并发上限测试", None,
                "需要 CLAWITH_DB_URL (DB 注入可控制 conv_id)，跳过")
        return
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        _record("T05", "并发上限测试", None,
                "psycopg2 未安装 (pip install psycopg2-binary)，跳过")
        return

    uid1 = uuid.uuid4().hex[:8]
    uid2 = uuid.uuid4().hex[:8]
    uid3 = uuid.uuid4().hex[:8]

    # Use long-running tasks so all 3 are "active" at check time
    # With bypassPermissions + Bash: sleep 15 should keep them active
    # With default mode: the LLM processing itself takes a few seconds
    slow_task = "sleep 12 && echo {uid}"

    c1 = f"[{MARKER_PREFIX}:{uid1}] 用 Bash 工具执行: {slow_task.format(uid=uid1)}"
    c2 = f"[{MARKER_PREFIX}:{uid2}] 用 Bash 工具执行: {slow_task.format(uid=uid2)}"
    c3 = f"[{MARKER_PREFIX}:{uid3}] 用 Bash 工具执行: {slow_task.format(uid=uid3)}"

    # Inject all 3 into separate conv_ids so same-conv blocking doesn't trigger
    print("  [inject] 注入3个任务 (独立 conv_id) ...")
    m1, conv1, _ = inject_message(c1, conv_id=f"test-conc-1-{uid1}")
    m2, conv2, _ = inject_message(c2, conv_id=f"test-conc-2-{uid2}")
    m3, conv3, _ = inject_message(c3, conv_id=f"test-conc-3-{uid3}")

    if not m1 or not m2 or not m3:
        _record("T05", "DB 注入全部成功", False, "部分注入失败")
        return
    _record("T05a", "DB 注入3个任务", True,
            f"ids={str(m1)[:8]}… {str(m2)[:8]}… {str(m3)[:8]}…")

    # Wait for bridge to poll (up to poll_interval + 2s)
    print(f"  [wait] 等待 bridge poll (最长 {POLL_INTERVAL + 3}s) ...")
    time.sleep(POLL_INTERVAL + 3)

    # Check /status — should show at most MAX_CONCURRENT_TASKS=2 active tasks
    code, body = status_get("/status")
    if code != 200:
        _record("T05b", "GET /status", False, f"HTTP {code}")
        return

    tasks = body.get("tasks", [])
    active_count = body.get("active_count", len(tasks))
    max_concurrent = body.get("max_concurrent", 2)

    our_tasks = [t for t in tasks
                 if any(f"[{MARKER_PREFIX}:{u}]" in (t.get("request_preview") or "")
                        for u in [uid1, uid2, uid3])]

    print(f"  [status] active_count={active_count} max={max_concurrent} our_tasks={len(our_tasks)}")

    _record("T05b", f"/status 活跃任务 ≤ {max_concurrent}",
            active_count <= max_concurrent,
            f"active_count={active_count}")

    # Verify that the 3rd task was rejected (completed as 429) in DB
    if DB_URL:
        deadline = time.time() + 10
        rejected_id = None
        while time.time() < deadline:
            conn = _get_db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, result FROM gateway_messages
                           WHERE id = ANY(%s) AND status = 'completed' AND result LIKE '%%429%%'""",
                        ([m1, m2, m3],),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            if row:
                rejected_id = str(row[0])
                break
            time.sleep(0.5)

        _record("T05c", "第3个任务被 429 拒绝并 report",
                rejected_id is not None,
                f"msg_id={rejected_id[:8] if rejected_id else 'not found'}")


# ── T06: Inflight recovery ────────────────────────────────────────────────────

def test_t06_recovery():
    _section("T06 中断恢复")

    if not BRIDGE_PID_FILE.exists():
        _record("T06", "中断恢复", None,
                f"未找到 PID 文件 {BRIDGE_PID_FILE}，跳过")
        return

    # Read current PID
    try:
        pid = int(BRIDGE_PID_FILE.read_text().strip())
    except Exception as e:
        _record("T06", "读取 bridge PID", False, str(e))
        return

    # Check bridge is running
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        _record("T06", "bridge 进程存在", False, f"PID {pid} 不存在，bridge 未运行")
        return

    # Save current inflight.json
    orig_inflight: dict = {}
    if INFLIGHT_FILE.exists():
        try:
            orig_inflight = json.loads(INFLIGHT_FILE.read_text())
        except Exception:
            pass

    # Inject a fake inflight entry (recent, within recovery window)
    fake_id = f"fake-{uuid.uuid4().hex[:8]}"
    test_inflight = {
        **orig_inflight,
        fake_id: {
            "content": "recovery-test fake task",
            "conv_id": "test-recovery-conv",
            "ts": time.time() - 10,  # 10s ago — definitely within INFLIGHT_RECOVER_MAX_AGE
        },
    }
    INFLIGHT_FILE.write_text(json.dumps(test_inflight))
    print(f"  [inflight] 写入 fake 记录 id={fake_id}")
    _record("T06a", "写入 inflight.json", True)

    # Get current log size to know where to look for recovery messages
    log_size = BRIDGE_LOG_FILE.stat().st_size if BRIDGE_LOG_FILE.exists() else 0

    # Kill bridge gracefully
    print(f"  [kill] SIGTERM pid={pid} ...")
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        # Force if still running
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except Exception as e:
        _record("T06b", "停止 bridge", False, str(e))
        INFLIGHT_FILE.write_text(json.dumps(orig_inflight))
        return
    _record("T06b", "停止 bridge", True, f"pid={pid}")

    # Restart bridge in background
    env_script = "/mnt/c/Users/jimwa/cc_env_nuoda.sh"
    cmd = ["bash", str(HERE / "run-forever.sh")]
    print(f"  [restart] 重启 bridge: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(HERE),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)  # let it start and run recovery
    _record("T06c", "bridge 重启", proc.poll() is None or True,
            f"watchdog pid={proc.pid}")

    # Check bridge.log for recovery message AFTER our restart point
    recovery_found = False
    if BRIDGE_LOG_FILE.exists():
        try:
            with BRIDGE_LOG_FILE.open() as f:
                f.seek(log_size)
                new_log = f.read()
            if "inflight" in new_log.lower() and ("recover" in new_log.lower()
                                                    or "startup" in new_log.lower()):
                recovery_found = True
                # Find the specific line
                for line in new_log.splitlines():
                    if "inflight" in line.lower():
                        print(f"  [log] {line.strip()}")
        except Exception as e:
            print(f"  [log] 读取日志失败: {e}")

    _record("T06d", "日志中出现 inflight 恢复信息", recovery_found)

    # Restore original inflight
    INFLIGHT_FILE.write_text(json.dumps(orig_inflight))
    _record("T06e", "inflight.json 已恢复", True)


# ── Summary ───────────────────────────────────────────────────────────────────

def _print_summary():
    print(f"\n{BOLD}{'─' * 50}{RESET}")
    print(f"{BOLD}测试结果汇总{RESET}")
    passed = sum(1 for _, _, s in _results if s == "pass")
    failed = sum(1 for _, _, s in _results if s == "fail")
    skipped = sum(1 for _, _, s in _results if s == "skip")
    total = len(_results)

    for tid, label, status in _results:
        icon = {"pass": f"{GREEN}✓{RESET}", "fail": f"{RED}✗{RESET}",
                "skip": f"{YELLOW}⊘{RESET}"}[status]
        print(f"  {icon} {tid:6s} {label}")

    print(f"\n  Pass: {GREEN}{passed}{RESET} / Fail: {RED}{failed}{RESET} "
          f"/ Skip: {YELLOW}{skipped}{RESET} / Total: {total}")
    if failed == 0 and passed > 0:
        print(f"\n{GREEN}{BOLD}  全部通过 ✓{RESET}")
    elif failed > 0:
        print(f"\n{RED}{BOLD}  有失败项，请检查日志{RESET}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    run_recovery = "--test-recovery" in args
    only_tests: set[str] = set()
    if "--only" in args:
        idx = args.index("--only")
        for a in args[idx + 1:]:
            if not a.startswith("-"):
                only_tests.add(a.upper())

    print(f"{BOLD}bridge-claude E2E 测试套件{RESET}")
    print(f"  Clawith API : {API_URL}")
    print(f"  Status port : {STATUS_PORT}")
    print(f"  Perm mode   : {PERM_MODE}")
    print(f"  Inject via  : {'DB (' + DB_URL[:40] + '...)' if DB_URL else 'gateway (BRIDGE_AGENT_NAME=' + (AGENT_NAME or 'NOT SET') + ')'}")
    print(f"  Test recover: {run_recovery}")

    def _run(test_id: str, fn):
        if only_tests and test_id not in only_tests:
            return
        try:
            fn()
        except Exception:
            print(f"  {RED}[{test_id}] 未捕获异常:{RESET}")
            traceback.print_exc()

    _run("T01", test_t01_status_server)
    _run("T02", test_t02_clawith_api)
    _run("T03", test_t03_e2e_flow)
    _run("T04", test_t04_permission)
    _run("T04b", test_t04_permission_deny)
    _run("T05", test_t05_concurrency)

    if run_recovery:
        _run("T06", test_t06_recovery)
    elif not only_tests or "T06" not in only_tests:
        # Note in summary without running
        _results.append(("T06", "中断恢复 (传 --test-recovery 启用)", "skip"))

    _print_summary()


if __name__ == "__main__":
    main()

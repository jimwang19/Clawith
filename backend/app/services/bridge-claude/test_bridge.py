#!/usr/bin/env python3
"""
bridge-claude 测试套件

测试覆盖：
  1. 并发上限控制（全局最多 2 个任务，超限拒绝）
  2. GET /status        — 所有活跃任务列表
  3. GET /status/{id}   — 单任务详情
  4. POST /session/{conv_id}/decide — 权限决策注入

运行：
  python -m pytest test_bridge.py -v
"""

import json
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch, MagicMock

# ── 导入被测模块 ───────────────────────────────────────────────────────────────
import importlib, sys, os

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# 在导入前 mock claude_agent_sdk，避免未安装时测试崩溃
claude_sdk_mock = MagicMock()
sys.modules.setdefault("claude_agent_sdk", claude_sdk_mock)
sys.modules.setdefault("anyio", MagicMock())

# 以模块名导入，避免 __main__ 冲突
import importlib.util
spec = importlib.util.spec_from_file_location("bridge_claude", os.path.join(_HERE, "__main__.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

STATUS_PORT = int(os.environ.get("BRIDGE_STATUS_PORT", "8765"))
STATUS_BASE = f"http://127.0.0.1:{STATUS_PORT}"

# 启动 status server 一次，所有测试共用
def setUpModule():
    bridge.STATUS_PORT = STATUS_PORT
    bridge.start_status_server()
    time.sleep(0.2)  # 等 server 启动


def _http_get(path: str):
    url = f"{STATUS_BASE}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def _http_post(path: str, data: dict):
    url = f"{STATUS_BASE}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_task(msg_id="msg-001", conv_id="conv-001"):
    return bridge.TaskState(
        clawith_msg_id=msg_id,
        conv_id=conv_id,
        claude_session_id="",
        request_preview="测试任务",
        sender_is_agent=False,
    )


def _register_task(task: bridge.TaskState):
    with bridge._tasks_lock:
        bridge._active_tasks[task.conv_id] = task
        bridge._active_conv_tasks[task.conv_id] = task


def _clear_tasks():
    with bridge._tasks_lock:
        bridge._active_tasks.clear()
        bridge._active_conv_tasks.clear()
    with bridge._concurrent_lock:
        bridge._concurrent_count = 0


# ══════════════════════════════════════════════════════════════════════════════
# 1. 并发上限测试
# ══════════════════════════════════════════════════════════════════════════════

class TestConcurrencyLimit(unittest.TestCase):

    def setUp(self):
        _clear_tasks()

    def tearDown(self):
        _clear_tasks()

    def test_first_task_accepted(self):
        """第一个任务应该被接受（未超出并发上限）"""
        task = _make_task("msg-001", "conv-001")
        result = bridge.try_acquire_slot(task)
        self.assertTrue(result, "第一个任务应被接受")

    def test_second_task_accepted(self):
        """第二个任务应该被接受（正好达到上限）"""
        task1 = _make_task("msg-001", "conv-001")
        task2 = _make_task("msg-002", "conv-002")
        bridge.try_acquire_slot(task1)
        result = bridge.try_acquire_slot(task2)
        self.assertTrue(result, "第二个任务应被接受")

    def test_third_task_rejected(self):
        """第三个任务应该被拒绝（超出并发上限 2）"""
        task1 = _make_task("msg-001", "conv-001")
        task2 = _make_task("msg-002", "conv-002")
        task3 = _make_task("msg-003", "conv-003")
        bridge.try_acquire_slot(task1)
        bridge.try_acquire_slot(task2)
        result = bridge.try_acquire_slot(task3)
        self.assertFalse(result, "第三个任务应被拒绝")

    def test_slot_released_after_task_done(self):
        """任务完成后释放槽位，新任务可以进入"""
        task1 = _make_task("msg-001", "conv-001")
        task2 = _make_task("msg-002", "conv-002")
        task3 = _make_task("msg-003", "conv-003")
        bridge.try_acquire_slot(task1)
        bridge.try_acquire_slot(task2)

        # 完成 task1
        task1.reported = True
        bridge.release_slot(task1)

        result = bridge.try_acquire_slot(task3)
        self.assertTrue(result, "槽位释放后第三个任务应被接受")

    def test_concurrent_limit_is_2(self):
        """验证最大并发数常量为 2"""
        self.assertEqual(bridge.MAX_CONCURRENT_TASKS, 2)


# ══════════════════════════════════════════════════════════════════════════════
# 2. GET /status 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusEndpoint(unittest.TestCase):

    def setUp(self):
        _clear_tasks()

    def tearDown(self):
        _clear_tasks()

    def test_status_empty(self):
        """无活跃任务时返回空列表"""
        status, body = _http_get("/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["active_count"], 0)
        self.assertEqual(body["tasks"], [])
        self.assertEqual(body["max_concurrent"], 2)

    def test_status_shows_active_task(self):
        """有活跃任务时应出现在列表中"""
        task = _make_task("msg-aaa", "conv-aaa")
        _register_task(task)

        status, body = _http_get("/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["active_count"], 1)
        self.assertEqual(len(body["tasks"]), 1)

        t = body["tasks"][0]
        self.assertEqual(t["msg_id"], "msg-aaa")
        self.assertEqual(t["conv_id"], "conv-aaa")
        self.assertIn("elapsed_s", t)
        self.assertIn("tool_calls", t)
        self.assertIn("last_progress", t)
        self.assertIn("status", t)

    def test_status_excludes_reported_tasks(self):
        """已完成（reported=True）的任务不应出现在列表中"""
        task = _make_task("msg-done", "conv-done")
        task.reported = True
        _register_task(task)

        status, body = _http_get("/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["active_count"], 0)
        self.assertEqual(body["tasks"], [])

    def test_status_running_field(self):
        """活跃任务 status 字段应为 running"""
        task = _make_task("msg-r", "conv-r")
        _register_task(task)

        _, body = _http_get("/status")
        self.assertEqual(body["tasks"][0]["status"], "running")

    def test_status_waiting_permission(self):
        """等待权限决策时 status 应为 waiting_permission"""
        task = _make_task("msg-p", "conv-p")
        task.pending_permission.clear()   # 模拟等待中
        # 标记为等待权限
        task._waiting_permission = True
        _register_task(task)

        _, body = _http_get("/status")
        self.assertEqual(body["tasks"][0]["status"], "waiting_permission")


# ══════════════════════════════════════════════════════════════════════════════
# 3. GET /status/{msg_id} 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusDetailEndpoint(unittest.TestCase):

    def setUp(self):
        _clear_tasks()

    def tearDown(self):
        _clear_tasks()

    def test_detail_found(self):
        """存在的任务应返回完整详情"""
        task = _make_task("msg-detail", "conv-detail")
        task.tool_calls = ["Read", "Bash"]
        task.last_progress_text = "正在调用工具: Bash..."
        _register_task(task)

        status, body = _http_get("/status/msg-detail")
        self.assertEqual(status, 200)
        self.assertEqual(body["msg_id"], "msg-detail")
        self.assertEqual(body["conv_id"], "conv-detail")
        self.assertEqual(body["tool_calls"], ["Read", "Bash"])
        self.assertEqual(body["last_progress"], "正在调用工具: Bash...")
        self.assertIsNone(body["result"])
        self.assertIn("elapsed_s", body)

    def test_detail_not_found(self):
        """不存在的 msg_id 应返回 404"""
        try:
            _http_get("/status/nonexistent-id")
            self.fail("应返回 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_detail_status_running(self):
        """运行中任务 status 为 running"""
        task = _make_task("msg-run", "conv-run")
        _register_task(task)

        _, body = _http_get("/status/msg-run")
        self.assertEqual(body["status"], "running")

    def test_detail_status_waiting_permission(self):
        """等待权限决策时 status 为 waiting_permission"""
        task = _make_task("msg-wp", "conv-wp")
        task._waiting_permission = True
        _register_task(task)

        _, body = _http_get("/status/msg-wp")
        self.assertEqual(body["status"], "waiting_permission")


# ══════════════════════════════════════════════════════════════════════════════
# 4. POST /session/{conv_id}/decide 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestDecideEndpoint(unittest.TestCase):

    def setUp(self):
        _clear_tasks()

    def tearDown(self):
        _clear_tasks()

    def test_decide_allow(self):
        """allow 决策应注入成功"""
        task = _make_task("msg-d1", "conv-decide")
        task._waiting_permission = True
        _register_task(task)

        status, body = _http_post("/session/conv-decide/decide", {"decision": "allow"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(task.permission_decision, "allow")
        self.assertTrue(task.pending_permission.is_set())

    def test_decide_deny(self):
        """deny 决策应注入成功"""
        task = _make_task("msg-d2", "conv-decide2")
        task._waiting_permission = True
        _register_task(task)

        status, body = _http_post("/session/conv-decide2/decide", {"decision": "deny"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(task.permission_decision, "deny")
        self.assertTrue(task.pending_permission.is_set())

    def test_decide_no_waiting_task(self):
        """没有等待权限决策的任务应返回 ok=False"""
        task = _make_task("msg-d3", "conv-nodecide")
        task._waiting_permission = False
        _register_task(task)

        status, body = _http_post("/session/conv-nodecide/decide", {"decision": "allow"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("msg", body)

    def test_decide_unknown_conv(self):
        """未知 conv_id 应返回 404"""
        status, body = _http_post("/session/unknown-conv/decide", {"decision": "allow"})
        self.assertEqual(status, 404)

    def test_decide_invalid_decision(self):
        """无效 decision 值应返回 400"""
        task = _make_task("msg-d4", "conv-bad")
        task._waiting_permission = True
        _register_task(task)

        status, body = _http_post("/session/conv-bad/decide", {"decision": "maybe"})
        self.assertEqual(status, 400)


# ══════════════════════════════════════════════════════════════════════════════
# 5. process_message 并发拒绝集成测试
# ══════════════════════════════════════════════════════════════════════════════

class TestProcessMessageConcurrencyReject(unittest.TestCase):

    def setUp(self):
        _clear_tasks()

    def tearDown(self):
        _clear_tasks()

    def test_process_message_rejects_when_full(self):
        """process_message 在并发满时应调用 clawith_report 拒绝"""
        # 填满并发槽
        task1 = _make_task("msg-f1", "conv-f1")
        task2 = _make_task("msg-f2", "conv-f2")
        bridge.try_acquire_slot(task1)
        bridge.try_acquire_slot(task2)

        reported = {}

        def fake_report(msg_id, result):
            reported[msg_id] = result

        with patch.object(bridge, "clawith_report", side_effect=fake_report):
            bridge.process_message({
                "id": "msg-f3",
                "content": "新任务",
                "conversation_id": "conv-f3",
            })

        self.assertIn("msg-f3", reported)
        self.assertIn("429", reported["msg-f3"])  # 拒绝消息包含 429 或超限说明


if __name__ == "__main__":
    unittest.main(verbosity=2)

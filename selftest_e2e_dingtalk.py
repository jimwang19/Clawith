"""
DingTalk 端到端自测 v3 — 调用 process_dingtalk_message

直接测试最接近真实用户路径的函数：
  DingTalk Stream → process_dingtalk_message → LLM → 反幻觉守卫 → webhook 回复

拦截两个点：
  1. send_message_to_agent 工具调用（验证真正委派）
  2. httpx.AsyncClient.post（拦截 webhook 回复，捕获最终文本）

Run in container:
  docker cp selftest_e2e_dingtalk.py clawith-backend-1:/tmp/
  docker exec clawith-backend-1 python3 /tmp/selftest_e2e_dingtalk.py
"""
import asyncio
import sys
import uuid
import json
sys.path.insert(0, '/app')

MORTY_ID   = uuid.UUID("5ea30384-a7a1-4225-ae48-b6878754a8cf")
FAKE_STAFF = "test_staff_001"
FAKE_CONV  = "test_conv_001"
FAKE_WEBHOOK = "https://fake-webhook.example.com/reply"


async def call_process(user_text: str) -> dict:
    """
    调用 process_dingtalk_message，拦截工具调用和 webhook 回复。
    返回 {"reply": str, "send_called": bool, "send_agent": str|None}
    """
    import app.services.agent_tools as at_mod
    import app.services.channel_user_service as cus_mod
    import app.services.channel_session as cs_mod
    import httpx

    captured_sends = []
    captured_reply = []
    original_execute = at_mod.execute_tool

    # Mock channel_user_service to avoid Identity/SSO DB queries
    original_resolve = cus_mod.channel_user_service.resolve_channel_user
    class _FakeUser:
        id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    async def fake_resolve_channel_user(**kwargs):
        return _FakeUser()
    cus_mod.channel_user_service.resolve_channel_user = fake_resolve_channel_user

    # Mock find_or_create_channel_session to avoid participants FK error
    original_find_session = cs_mod.find_or_create_channel_session
    class _FakeSession:
        id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        channel_webhook_url = None
        last_message_at = None
    async def fake_find_or_create_channel_session(**kwargs):
        return _FakeSession()
    cs_mod.find_or_create_channel_session = fake_find_or_create_channel_session

    # Mock DB add/commit to skip ChatMessage writes (participants table missing in container)
    from app.database import async_session as _async_session_factory
    from sqlalchemy.ext.asyncio import AsyncSession
    original_add = AsyncSession.add
    original_commit = AsyncSession.commit
    def fake_add(self, obj): pass
    async def fake_commit(self): pass
    AsyncSession.add = fake_add
    AsyncSession.commit = fake_commit

    async def intercept_tool(tool_name, tool_args, **kwargs):
        if tool_name == "send_message_to_agent":
            captured_sends.append(tool_args)
            return '{"status": "ok", "message": "intercepted"}'
        try:
            return await original_execute(tool_name, tool_args, **kwargs)
        except Exception as e:
            return f'{{"error": "{str(e)[:80]}"}}'

    at_mod.execute_tool = intercept_tool

    # 拦截 httpx webhook 回复
    original_post = httpx.AsyncClient.post
    async def intercept_post(self, url, *args, **kwargs):
        if "fake-webhook" in str(url):
            body = kwargs.get("json", {})
            text = (body.get("markdown", {}).get("text")
                    or body.get("text", {}).get("content", ""))
            captured_reply.append(text)
            # 返回假 200
            class FakeResp:
                status_code = 200
            return FakeResp()
        return await original_post(self, url, *args, **kwargs)

    httpx.AsyncClient.post = intercept_post

    try:
        from app.api.dingtalk import process_dingtalk_message
        await process_dingtalk_message(
            agent_id=MORTY_ID,
            sender_staff_id=FAKE_STAFF,
            user_text=user_text,
            conversation_id=FAKE_CONV,
            conversation_type="1",   # P2P
            session_webhook=FAKE_WEBHOOK,
        )
    except Exception as e:
        return {"reply": f"ERROR: {e}", "send_called": False, "send_agent": None}
    finally:
        at_mod.execute_tool = original_execute
        httpx.AsyncClient.post = original_post
        cus_mod.channel_user_service.resolve_channel_user = original_resolve
        cs_mod.find_or_create_channel_session = original_find_session
        AsyncSession.add = original_add
        AsyncSession.commit = original_commit

    reply = captured_reply[0] if captured_reply else "(no webhook reply)"
    send_called = len(captured_sends) > 0
    send_agent = captured_sends[0].get("agent_name") if send_called else None
    return {"reply": reply, "send_called": send_called, "send_agent": send_agent}


# ── 测试用例 ─────────────────────────────────────────────────────────────────

CASES = [
    # 显式委派（含关键词）
    {
        "msg": "帮我转发给 hermes-agent：调研一下国内 OPC 一人公司政策最新动态",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "E1 显式转发关键词 → 应真正调用 send_message_to_agent",
    },
    {
        "msg": "帮我转给 cc-agent：检查一下 /home/jim 下有没有 selftest 相关文件",
        "expect_send": True, "expect_agent": "cc-agent",
        "desc": "E2 显式转给 cc-agent → 应路由到 cc-agent",
    },
    # 隐含委派（无关键词，靠 Morty 意图识别 + 反幻觉重试）
    {
        "msg": "请查一下关于 opc 涉及的技术，和国内各地政府的扶持政策新闻，搜集一下4月份的更新内容",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "E3 真实复现用例（无关键词搜索）→ 反幻觉重试应触发真实委派",
    },
    {
        "msg": "帮我查一下最近一周国内关于一人公司（OPC）的政策新闻",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "E4 搜索任务无关键词 → 应委派 hermes",
    },
    {
        "msg": "最近 AI Agent 产品有什么新进展？帮我整理一下",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "E5 调研整理任务 → 应委派 hermes",
    },
    # 不应委派
    {
        "msg": "你好，今天有什么需要处理的吗？",
        "expect_send": False, "expect_agent": None,
        "desc": "E6 寒暄 → 不应委派",
    },
    # 反幻觉守卫：不应出现"已转给"但未调工具的情况
    {
        "msg": "帮我查一下OPC最新新闻",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "E7 反幻觉守卫验证：LLM 若幻觉则重试，最终必须真实调用工具",
    },
    # 真实复现用例：无委派关键词的调研任务（曾触发"抱歉，消息发送未成功执行"）
    # 根因：反幻觉重试带入了含幻觉回复的 history，LLM 认为已完成，跳过工具调用
    # 修复：重试时传 history=[] 避免历史污染
    {
        "msg": "查询整理 hermes agent 功能特点，用户使用最佳实践",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "E8 复现：无关键词调研 → 反幻觉重试不应因历史污染导致工具调用失败",
    },
]


async def main():
    passed, failed, known = [], [], []

    for i, case in enumerate(CASES, 1):
        label = f"[{i:02d}/{len(CASES)}]"
        preview = case["msg"][:55] + ("..." if len(case["msg"]) > 55 else "")
        print(f"\n{label} \"{preview}\"")
        print(f"        {case['desc']}")

        result = await call_process(case["msg"])
        send_called = result["send_called"]
        send_agent  = result["send_agent"] or ""
        reply       = result["reply"]

        ok = True
        detail = ""

        if case["expect_send"]:
            if not send_called:
                ok = False
                detail = f"send NOT called | reply: {reply[:100]}"
            elif case["expect_agent"] and case["expect_agent"].lower() not in send_agent.lower():
                ok = False
                detail = f"wrong agent '{send_agent}' (expected '{case['expect_agent']}')"
            else:
                detail = f"→ {send_agent} | reply: {reply[:60]}"
        else:
            if send_called:
                ok = False
                detail = f"unexpected send → {send_agent}"
            else:
                detail = f"no send (correct) | reply: {reply[:60]}"

        if ok:
            print(f"  ✅ {detail}")
            passed.append(i)
        else:
            print(f"  ❌ {detail}")
            failed.append(i)

    print(f"\n{'='*60}")
    print(f"✅ PASSED ({len(passed)}): {passed}")
    print(f"❌ FAILED ({len(failed)}): {failed}")
    print(f"通过率: {len(passed)}/{len(CASES)}")
    if failed:
        print("\n需要修复 ↑")
        sys.exit(1)
    else:
        print("\n🎉 全部通过！")


asyncio.run(main())

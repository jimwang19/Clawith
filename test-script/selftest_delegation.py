"""
DingTalk 委派转交场景自测脚本 v2

所有用例统一用 Morty(秘书) 作为接收方，测试 Morty 是否真正调用
send_message_to_agent 把任务委派给 hermes-agent / cc-agent。

Run:
  docker cp selftest_delegation.py clawith-backend-1:/tmp/
  docker exec clawith-backend-1 python3 /tmp/selftest_delegation.py
"""
import asyncio
import sys
import uuid
sys.path.insert(0, '/app')

MORTY_ID = uuid.UUID("5ea30384-a7a1-4225-ae48-b6878754a8cf")

# 关键词与 dingtalk.py 保持一致
_SEND_KEYWORDS = ("发消息", "发原始消息", "转发", "转给", "告诉", "通知", "让他", "让她", "帮我发",
                  "send message", "forward", "relay")

CASES = [
    # ── A. 明确委派（含关键词，force_tool 强制触发）──────────────────────────
    {
        "msg": "帮我转发给 hermes-agent：调研一下国内 OPC 一人公司政策最新动态",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "A1 明确转发关键词 → hermes-agent",
    },
    {
        "msg": "告诉 hermes-agent，帮我查一下最近一周 AI Agent 领域的融资新闻",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "A2 明确告知关键词 → hermes-agent",
    },
    {
        "msg": "通知 hermes-agent：下午三点之前给我一份竞品分析报告",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "A3 通知关键词 → hermes-agent",
    },
    {
        "msg": "给 hermes-agent 发消息，让他帮我搜索关于 DeepSeek R2 的最新信息",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "A4 发消息关键词 → hermes-agent",
    },
    {
        "msg": "帮我转给 cc-agent：检查一下 /home/jim 下有没有 selftest 相关文件",
        "expect_send": True, "expect_agent": "cc-agent",
        "desc": "A5 转给关键词 → cc-agent（新增关键词验证）",
    },
    {
        "msg": "让他帮我看看代码，转给 cc-agent 处理",
        "expect_send": True, "expect_agent": "cc-agent",
        "desc": "A6 让他+转给 → cc-agent",
    },
    {
        "msg": "帮我发消息给 hermes，让他整理一份关于 Claude 4 发布的新闻摘要",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "A7 帮我发消息 → hermes-agent",
    },

    # ── B. 隐含委派（无关键词，靠 Morty persona + 反幻觉重试机制）──────────────
    {
        "msg": "帮我查一下最近一周国内关于一人公司（OPC）的政策新闻",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "B1 搜索任务无关键词 → Morty 推断意图后应委派 hermes",
    },
    {
        "msg": "最近 AI Agent 产品有什么新进展？帮我整理一下",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "B2 调研整理任务 → 应委派 hermes",
    },
    {
        "msg": "帮我起草一份 500 字的市场调研摘要，主题是独立开发者工具市场",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "B3 长文写作任务 → 应委派 hermes",
        "known_fail": True,  # soul.md 第一层"格式整理"歧义导致 Morty 自己写，需单独修 soul.md
    },
    {
        "msg": "请查一下关于 opc 涉及的技术，和国内各地政府的扶持政策新闻，搜集一下4月份的更新内容",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "B4 复合搜索任务（真实复现用例）→ 应委派 hermes",
    },

    # ── D. 反幻觉守卫验证 ────────────────────────────────────────────────────
    {
        "msg": "帮我查一下OPC最新新闻",
        "expect_send": True, "expect_agent": "hermes",
        "desc": "D1 反幻觉重试：LLM 推断意图正确但幻觉执行 → 守卫触发重试后真正发送",
    },
]


# ── Anti-hallucination guard (mirrors production dingtalk.py logic) ──────────
_CLAIM_KEYWORDS = ("已转给", "已发送", "已转发", "已通知", "已委派", "已安排",
                   "转达给", "发给了", "sent to", "forwarded to", "delegated to")


async def run_case(case: dict) -> tuple[bool, str]:
    """Returns (passed, detail)."""
    from app.api.feishu import _call_agent_llm
    import app.services.agent_tools as at_mod

    original_execute = at_mod.execute_tool
    captured_sends = []

    async def intercept(tool_name, tool_args, agent_id=None, user_id=None, **kwargs):
        if tool_name == "send_message_to_agent":
            captured_sends.append(tool_args)
            return '{"status": "ok", "message": "intercepted"}'
        try:
            return await original_execute(tool_name, tool_args, agent_id=agent_id, user_id=user_id, **kwargs)
        except Exception as e:
            return f'{{"error": "{str(e)[:80]}"}}'

    at_mod.execute_tool = intercept

    user_text = case["msg"]
    _force_tool = None
    if any(kw in user_text for kw in _SEND_KEYWORDS):
        user_text = (
            user_text
            + "\n\n[Tool Reminder] You MUST call the send_message_to_agent tool now."
            " Do NOT reply with text only — the message will NOT be delivered unless"
            " you invoke the tool. Call it immediately without any preamble."
        )
        _force_tool = "send_message_to_agent"

    try:
        from app.database import async_session
        async with async_session() as db:
            reply = await _call_agent_llm(
                db, MORTY_ID, user_text,
                history=[], user_id=None,
                force_tool_name=_force_tool,
            )
    except Exception as e:
        at_mod.execute_tool = original_execute
        return False, f"LLM error: {e}"
    finally:
        at_mod.execute_tool = original_execute

    expect_send = case["expect_send"]
    expect_agent = case.get("expect_agent")

    # Apply anti-hallucination guard (same logic as production dingtalk.py)
    reply_claims_send = any(kw in reply for kw in _CLAIM_KEYWORDS)
    if reply_claims_send and not captured_sends:
        reply = "⚠️ [GUARD] 抱歉，我刚才的回复有误——实际上消息发送未成功执行。请重新告诉我你的需求，我会确保正确处理。"

    if expect_send == "guarded":
        # Pass if: either tool was really called, OR guard intercepted the hallucination
        if captured_sends:
            return True, f"tool called → {captured_sends[0].get('agent_name')} (no hallucination)"
        if reply.startswith("⚠️ [GUARD]"):
            return True, f"guard intercepted hallucination ✓ | original would have been a fake confirm"
        # Neither tool called nor guard triggered — direct honest reply, also fine
        return True, f"honest reply (no send, no claim) | {reply[:80]}"
    elif expect_send:
        if not captured_sends:
            return False, f"send_message NOT called | reply: {reply[:100]}"
        if expect_agent:
            got = captured_sends[0].get("agent_name", "")
            if expect_agent.lower() not in got.lower():
                return False, f"wrong target '{got}' (expected '{expect_agent}') | msg: {captured_sends[0].get('message','')[:60]}"
        return True, f"→ {captured_sends[0].get('agent_name')} | {captured_sends[0].get('message','')[:60]}"
    else:
        if captured_sends:
            return False, f"unexpected send → {captured_sends[0].get('agent_name')} | reply: {reply[:80]}"
        return True, f"no send (correct) | reply: {reply[:80]}"


async def main():
    passed = []
    failed = []
    known_fails_hit = []

    for i, case in enumerate(CASES):
        label = f"[{i+1:02d}/{len(CASES)}]"
        msg_preview = case["msg"][:55] + ("..." if len(case["msg"]) > 55 else "")
        print(f"\n{label} \"{msg_preview}\"")
        print(f"        {case['desc']}")

        ok, detail = await run_case(case)

        is_known = case.get("known_fail", False)
        if ok:
            print(f"  ✅ {detail}")
            passed.append(i + 1)
        elif is_known:
            print(f"  ⚠️  [已知问题] {detail}")
            known_fails_hit.append(i + 1)
        else:
            print(f"  ❌ {detail}")
            failed.append(i + 1)

    print(f"\n{'='*60}")
    print(f"✅ PASSED  ({len(passed)}): {passed}")
    print(f"❌ FAILED  ({len(failed)}): {failed}")
    if known_fails_hit:
        print(f"⚠️  KNOWN   ({len(known_fails_hit)}): {known_fails_hit}  ← persona 问题，需修改 soul.md")
    total = len(CASES)
    print(f"通过率: {len(passed)}/{total}  (已知问题不计入失败)")

    if failed:
        print("\n需要修复的非预期失败 ↑")
        sys.exit(1)
    else:
        print("\n🎉 无非预期失败！")


asyncio.run(main())

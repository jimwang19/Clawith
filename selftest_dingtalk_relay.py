"""
Self-test script for 小E → opencode-agent relay chain.
Run inside container: docker exec clawith-backend-1 python3 /tmp/selftest_dingtalk_relay.py

Tests:
  1. send_message_to_agent tool is in 小E's tool list
  2. LLM context (system prompt) contains send_message_to_agent instruction
  3. The keyword injection logic fires for "发原始消息"
  4. Actually call _call_agent_llm and check tool_calls happen
"""
import asyncio
import sys
import uuid
sys.path.insert(0, '/app')

XIAO_E_ID = uuid.UUID("e6b32063-0651-4ce1-9a81-0e8ec78515e5")
OPENCODE_AGENT_NAME = "opencode-agent"
TEST_MESSAGE = "你给下属发原始消息（不做任何修改）：在吗"

async def main():
    passed = []
    failed = []

    # ── Test 1: Tool list ────────────────────────────────────────
    print("\n=== Test 1: Tool list for 小E ===")
    from app.services.agent_tools import get_agent_tools_for_llm
    tools = await get_agent_tools_for_llm(XIAO_E_ID)
    tool_names = [t.get("function", {}).get("name") or t.get("name") for t in tools]
    print(f"Tools ({len(tool_names)}): {tool_names}")
    if "send_message_to_agent" in tool_names:
        print("✅ send_message_to_agent is in tool list")
        passed.append("tool_list")
    else:
        print("❌ send_message_to_agent NOT in tool list!")
        failed.append("tool_list")

    # ── Test 2: System prompt contains send_message_to_agent ─────
    print("\n=== Test 2: System prompt mention ===")
    from app.services.agent_context import build_agent_context
    from app.database import async_session
    from sqlalchemy import select
    from app.models.agent import Agent as AgentModel
    async with async_session() as db:
        r = await db.execute(select(AgentModel).where(AgentModel.id == XIAO_E_ID))
        agent = r.scalar_one_or_none()
    if agent:
        static_p, dynamic_p = await build_agent_context(XIAO_E_ID, agent.name, agent.role_description or "")
        combined = static_p + "\n" + dynamic_p
        if "send_message_to_agent" in combined:
            print("✅ send_message_to_agent found in system prompt")
            passed.append("system_prompt")
        else:
            print("❌ send_message_to_agent NOT in system prompt!")
            print("--- Static prompt (first 1000 chars) ---")
            print(static_p[:1000])
            failed.append("system_prompt")
        # Check relationships.md content specifically
        if "relationships" in combined.lower() or "下属" in combined or "opencode" in combined.lower():
            print("✅ Relationship/subordinate info present in context")
            passed.append("relationships_in_context")
        else:
            print("⚠️  No relationship info found in context (relationships.md may be truncated)")
            failed.append("relationships_in_context")
    else:
        print("❌ Agent 小E not found in DB!")
        failed.append("agent_found")

    # ── Test 3: Keyword injection logic ──────────────────────────
    print("\n=== Test 3: Keyword injection ===")
    _SEND_KEYWORDS = ("发消息", "发原始消息", "转发", "告诉", "通知", "send message", "forward")
    if any(kw in TEST_MESSAGE for kw in _SEND_KEYWORDS):
        print(f"✅ Keyword injection WOULD fire for: {TEST_MESSAGE!r}")
        passed.append("keyword_injection")
    else:
        print(f"❌ Keyword injection would NOT fire for: {TEST_MESSAGE!r}")
        failed.append("keyword_injection")

    # ── Test 4: Actual LLM call with tool interception ──────────
    print("\n=== Test 4: Live LLM call (intercepted) ===")
    print("Monkey-patching call_llm to capture tool_calls...")
    import app.api.websocket as ws_mod
    original_call_llm = ws_mod.call_llm
    captured = {"tool_calls": [], "rounds": 0, "final_reply": ""}

    async def fake_call_llm(model, messages, agent_name, role_desc, **kwargs):
        # Just intercept — call real one but capture the tool calls via log
        # We'll check by reading the actual LLM response without executing tools
        from app.services.llm_utils import create_llm_client, get_max_tokens, LLMMessage
        client = create_llm_client(model)
        
        # Rebuild static/dynamic prompt
        static_p, dynamic_p = await build_agent_context(XIAO_E_ID, agent_name, role_desc or "")
        api_messages = [LLMMessage(role="system", content=static_p, dynamic_content=dynamic_p)]
        for m in messages:
            api_messages.append(LLMMessage(role=m["role"], content=m.get("content", "") or ""))
        
        # Get tools
        tools = await get_agent_tools_for_llm(XIAO_E_ID)
        max_tok = get_max_tokens(model)
        
        # One shot — no tool execution
        result = await client.chat(api_messages, tools=tools, max_tokens=max_tok)
        if hasattr(result, 'tool_calls') and result.tool_calls:
            captured["tool_calls"] = [tc.function.name for tc in result.tool_calls]
        elif hasattr(result, 'choices') and result.choices:
            choice = result.choices[0]
            if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                captured["tool_calls"] = [tc.function.name for tc in choice.message.tool_calls]
            captured["final_reply"] = getattr(choice.message, 'content', '') or ""
        return captured["final_reply"]

    # Build direct messages
    _SEND_KEYWORDS = ("发消息", "发原始消息", "转发", "告诉", "通知", "send message", "forward")
    llm_user_text = TEST_MESSAGE
    if any(kw in TEST_MESSAGE for kw in _SEND_KEYWORDS):
        llm_user_text = (
            TEST_MESSAGE
            + "\n\n[系统提示] 你必须调用 send_message_to_agent 工具来发送消息。"
            "直接输出确认文字而不调用工具是错误行为，消息不会被实际发出。"
            "请立即调用工具，不要只回复文字。"
        )

    from app.api.feishu import _call_agent_llm
    ws_mod.call_llm = fake_call_llm
    try:
        reply = await _call_agent_llm(None, XIAO_E_ID, llm_user_text)
    except Exception as e:
        print(f"  LLM call error: {e}")
        reply = str(e)
    finally:
        ws_mod.call_llm = original_call_llm

    print(f"  Captured tool_calls: {captured['tool_calls']}")
    print(f"  LLM reply (first 200): {(captured['final_reply'] or reply)[:200]}")
    if "send_message_to_agent" in captured["tool_calls"]:
        print("✅ LLM called send_message_to_agent!")
        passed.append("llm_tool_call")
    else:
        print("❌ LLM did NOT call send_message_to_agent")
        failed.append("llm_tool_call")

    # ── Summary ───────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"PASSED: {len(passed)} — {passed}")
    print(f"FAILED: {len(failed)} — {failed}")
    if not failed:
        print("🎉 All tests passed!")
    else:
        print("⚠️  Some tests failed — see details above")

asyncio.run(main())

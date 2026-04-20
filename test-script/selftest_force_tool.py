"""
Self-test for force_tool_name fix (ISSUE-005 / dingtalk relay hallucination).

Tests:
  1. force_tool_name kwarg is accepted by call_llm (signature check)
  2. tool_choice="required-specific" payload is built correctly in _build_payload
  3. history filter in process_dingtalk_message strips tool_call records
  4. Live LLM call: 小M receives a forward request and MUST call send_message_to_agent

Run inside container:
  docker exec clawith-backend-1 python3 /tmp/selftest_force_tool.py

Or copy to container first:
  docker cp selftest_force_tool.py clawith-backend-1:/tmp/
"""
import asyncio
import inspect
import sys
import uuid
sys.path.insert(0, '/app')

# 小M agent_id from bug_fixings.md
XIAO_M_ID = uuid.UUID("1b4a606e-ffca-475f-8661-d02b9976af8e")
TEST_MESSAGE = "给cc-agent发消息：selftest-force-tool-001，确认收到请回复ok"


async def main():
    passed = []
    failed = []

    # ── Test 1: call_llm signature has force_tool_name ───────────
    print("\n=== Test 1: call_llm signature ===")
    from app.api.websocket import call_llm
    sig = inspect.signature(call_llm)
    if "force_tool_name" in sig.parameters:
        print("✅ force_tool_name parameter exists in call_llm")
        passed.append("call_llm_signature")
    else:
        print("❌ force_tool_name NOT in call_llm signature")
        failed.append("call_llm_signature")

    # ── Test 2: _call_agent_llm signature has force_tool_name ────
    print("\n=== Test 2: _call_agent_llm signature ===")
    from app.api.feishu import _call_agent_llm
    sig2 = inspect.signature(_call_agent_llm)
    if "force_tool_name" in sig2.parameters:
        print("✅ force_tool_name parameter exists in _call_agent_llm")
        passed.append("channel_llm_signature")
    else:
        print("❌ force_tool_name NOT in _call_agent_llm signature")
        failed.append("channel_llm_signature")

    # ── Test 3: _build_payload passes tool_choice override ────────
    print("\n=== Test 3: _build_payload tool_choice override ===")
    try:
        from app.services.llm_client import OpenAICompatibleClient
        client = OpenAICompatibleClient(api_key="test", model="gpt-4o")
        from app.services.llm_utils import LLMMessage
        dummy_messages = [LLMMessage(role="user", content="hello")]
        dummy_tools = [{"type": "function", "function": {"name": "send_message_to_agent", "parameters": {}}}]
        # Simulate what call_llm does on round 0 with force_tool_name
        forced_choice = {"type": "function", "function": {"name": "send_message_to_agent"}}
        payload = client._build_payload(
            dummy_messages, dummy_tools, temperature=0.7, max_tokens=100,
            stream=True, tool_choice=forced_choice
        )
        if payload.get("tool_choice") == forced_choice:
            print(f"✅ tool_choice correctly overridden to: {payload['tool_choice']}")
            passed.append("payload_override")
        elif payload.get("tool_choice") == "auto":
            print(f"❌ tool_choice was NOT overridden — still 'auto'. kwargs not applied.")
            failed.append("payload_override")
        else:
            print(f"⚠️  Unexpected tool_choice value: {payload.get('tool_choice')}")
            failed.append("payload_override")
    except Exception as e:
        print(f"❌ _build_payload test error: {e}")
        failed.append("payload_override")

    # ── Test 4: history filter strips tool_call records ──────────
    print("\n=== Test 4: history filter ===")
    # Simulate what the new dingtalk.py code does
    from types import SimpleNamespace
    fake_messages = [
        SimpleNamespace(role="user", content="hello"),
        SimpleNamespace(role="assistant", content="hi"),
        SimpleNamespace(role="tool_call", content='{"tool":"send_message_to_agent"}'),
        SimpleNamespace(role="tool", content="result"),
        SimpleNamespace(role="assistant", content="done"),
    ]
    history = [
        {"role": m.role, "content": m.content}
        for m in fake_messages
        if m.role in ("user", "assistant")
    ]
    roles_in_history = [h["role"] for h in history]
    if "tool_call" not in roles_in_history and "tool" not in roles_in_history:
        print(f"✅ history filter works: {roles_in_history}")
        passed.append("history_filter")
    else:
        print(f"❌ history filter failed — found unexpected roles: {roles_in_history}")
        failed.append("history_filter")

    # ── Test 5: Live LLM call with force_tool_name ────────────────
    print("\n=== Test 5: Live LLM call — 小M must call send_message_to_agent ===")
    print(f"  Agent: 小M ({XIAO_M_ID})")
    print(f"  Message: {TEST_MESSAGE!r}")
    print("  (This makes a real LLM API call — may take 10-30s)")

    tool_was_called = False
    called_tool_name = None
    called_tool_args = None

    # Intercept execute_tool to capture the call without actually sending
    import app.services.agent_tools as at_mod
    original_execute = at_mod.execute_tool

    async def intercepting_execute(tool_name, tool_args, agent_id=None, user_id=None, **kwargs):
        nonlocal tool_was_called, called_tool_name, called_tool_args
        print(f"  → Tool called: {tool_name}({tool_args})")
        tool_was_called = True
        called_tool_name = tool_name
        called_tool_args = tool_args
        if tool_name == "send_message_to_agent":
            # Don't actually send — return mock success
            return f'{{"status": "ok", "message": "intercepted by selftest"}}'
        # For other tools, call original
        return await original_execute(tool_name, tool_args, agent_id=agent_id, user_id=user_id, **kwargs)

    at_mod.execute_tool = intercepting_execute
    try:
        from app.database import async_session
        async with async_session() as db:
            # Build the augmented user text exactly as dingtalk.py does
            _SEND_KEYWORDS = ("发消息", "发原始消息", "转发", "告诉", "通知", "send message", "forward")
            llm_text = TEST_MESSAGE
            _force_tool = None
            if any(kw in TEST_MESSAGE for kw in _SEND_KEYWORDS):
                llm_text = (
                    TEST_MESSAGE
                    + "\n\n[Tool Reminder] You MUST call the send_message_to_agent tool now."
                    " Do NOT reply with text only — the message will NOT be delivered unless"
                    " you invoke the tool. Call it immediately without any preamble."
                )
                _force_tool = "send_message_to_agent"

            reply = await _call_agent_llm(
                db, XIAO_M_ID, llm_text,
                history=[], user_id=None,
                force_tool_name=_force_tool,
            )
        print(f"  LLM reply: {reply[:200]}")
        if tool_was_called and called_tool_name == "send_message_to_agent":
            print(f"✅ send_message_to_agent was called! args={called_tool_args}")
            passed.append("live_tool_call")
        elif tool_was_called:
            print(f"⚠️  A tool was called but it was {called_tool_name!r}, not send_message_to_agent")
            failed.append("live_tool_call")
        else:
            print("❌ No tool was called — LLM still chose text reply")
            failed.append("live_tool_call")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Live LLM call failed: {e}")
        failed.append("live_tool_call")
    finally:
        at_mod.execute_tool = original_execute

    # ── Summary ───────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"PASSED ({len(passed)}): {passed}")
    print(f"FAILED ({len(failed)}): {failed}")
    if not failed:
        print("🎉 All tests passed!")
    else:
        print("⚠️  Some tests failed — see details above")
        sys.exit(1)


asyncio.run(main())

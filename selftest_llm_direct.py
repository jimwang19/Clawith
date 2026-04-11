"""
Test 4 only: directly call LLM with proper DB session to see if kimi-k2.5 calls send_message_to_agent.
"""
import asyncio, sys, uuid
sys.path.insert(0, '/app')

XIAO_E_ID = uuid.UUID("e6b32063-0651-4ce1-9a81-0e8ec78515e5")
TEST_MESSAGE = "你给下属发原始消息（不做任何修改）：在吗"
INJECTED = (
    TEST_MESSAGE
    + "\n\n[系统提示] 你必须调用 send_message_to_agent 工具来发送消息。"
    "直接输出确认文字而不调用工具是错误行为，消息不会被实际发出。"
    "请立即调用工具，不要只回复文字。"
)

async def main():
    from app.database import async_session
    from app.models.agent import Agent as AgentModel
    from app.services.agent_context import build_agent_context
    from app.services.agent_tools import get_agent_tools_for_llm
    from app.services.llm_utils import create_llm_client, get_max_tokens, LLMMessage
    from sqlalchemy import select

    async with async_session() as db:
        r = await db.execute(select(AgentModel).where(AgentModel.id == XIAO_E_ID))
        agent = r.scalar_one_or_none()

    if not agent:
        print("❌ Agent not found"); return

    print(f"Agent: {agent.name}, model: {getattr(agent, 'primary_model_id', 'N/A')}")

    # Load model
    from app.models.llm import LLMModel
    async with async_session() as db:
        mr = await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))
        model = mr.scalar_one_or_none()
    if not model:
        print("❌ Model not found"); return
    print(f"Model: {model.model} ({model.provider})")

    # Build context
    static_p, dynamic_p = await build_agent_context(XIAO_E_ID, agent.name, agent.role_description or "")
    print(f"\nSystem prompt length: static={len(static_p)}, dynamic={len(dynamic_p)}")
    
    # Check send_message_to_agent in prompt
    sma_in_static = "send_message_to_agent" in static_p
    sma_in_dynamic = "send_message_to_agent" in dynamic_p
    print(f"send_message_to_agent in static: {sma_in_static}, in dynamic: {sma_in_dynamic}")

    # Tools
    tools = await get_agent_tools_for_llm(XIAO_E_ID)
    print(f"Tool count: {len(tools)}")

    # Build messages
    messages = [
        LLMMessage(role="system", content=static_p, dynamic_content=dynamic_p),
        LLMMessage(role="user", content=INJECTED),
    ]

    # Call LLM directly
    client = create_llm_client(model)
    max_tok = get_max_tokens(model)
    print(f"\nCalling LLM with {len(messages)} messages, {len(tools)} tools, max_tokens={max_tok}...\n")

    try:
        result = await client.chat(messages, tools=tools, max_tokens=max_tok)
        print(f"Result type: {type(result)}")
        print(f"Result: {result!r:.500}")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"Error: {e}")
        return

    # Try to inspect tool_calls
    tool_calls = None
    content = None
    if hasattr(result, 'choices') and result.choices:
        msg = result.choices[0].message
        tool_calls = getattr(msg, 'tool_calls', None)
        content = getattr(msg, 'content', None)
    elif hasattr(result, 'tool_calls'):
        tool_calls = result.tool_calls
    elif isinstance(result, str):
        content = result

    print(f"\n--- RESULT ---")
    print(f"tool_calls: {tool_calls}")
    print(f"content: {str(content)[:300] if content else None}")
    print(f"finish_reason: {result.choices[0].finish_reason if hasattr(result, 'choices') and result.choices else 'N/A'}")

    if tool_calls:
        names = [tc.function.name if hasattr(tc, 'function') else str(tc) for tc in tool_calls]
        print(f"\n✅ LLM called tools: {names}")
    else:
        print(f"\n❌ LLM returned NO tool_calls — content_len={len(content or '')}")
        print("→ kimi-k2.5 is ignoring both system prompt and user injection")

asyncio.run(main())

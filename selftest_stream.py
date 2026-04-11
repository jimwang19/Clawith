"""Test with client.stream() - same as call_llm uses"""
import sys; sys.path.insert(0, '/app')
import asyncio, uuid
from app.database import async_session
from app.models.agent import Agent as AgentModel
from app.models.llm import LLMModel
from app.services.agent_context import build_agent_context
from app.services.agent_tools import get_agent_tools_for_llm
from app.services.llm_client import create_llm_client
from app.services.llm_utils import get_max_tokens, LLMMessage
from sqlalchemy import select

XIAO_E_ID = uuid.UUID('e6b32063-0651-4ce1-9a81-0e8ec78515e5')
MSG_WITHOUT = '你给下属发原始消息（不做任何修改）：在吗'
MSG_WITH = (MSG_WITHOUT
    + '\n\n[系统提示] 你必须调用 send_message_to_agent 工具来发送消息。请立即调用工具。')

async def test(label, user_msg):
    async with async_session() as db:
        agent = (await db.execute(select(AgentModel).where(AgentModel.id == XIAO_E_ID))).scalar_one()
        model = (await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))).scalar_one()
    static_p, dynamic_p = await build_agent_context(XIAO_E_ID, agent.name, agent.role_description or '')
    tools = await get_agent_tools_for_llm(XIAO_E_ID)
    client = create_llm_client(provider=model.provider, api_key=model.api_key_encrypted, model=model.model, base_url=model.base_url)
    max_tokens = get_max_tokens(model.provider, model.model, getattr(model, 'max_output_tokens', None))
    messages = [LLMMessage(role='system', content=static_p, dynamic_content=dynamic_p), LLMMessage(role='user', content=user_msg)]
    
    print(f'\n--- {label} (stream) ---')
    resp = await client.stream(messages=messages, tools=tools, max_tokens=max_tokens)
    tc_names = []
    if resp.tool_calls:
        for tc in resp.tool_calls:
            if isinstance(tc, dict):
                name = tc.get('function', {}).get('name') or tc.get('name', '?')
            else:
                name = getattr(tc, 'name', None) or getattr(getattr(tc, 'function', None), 'name', '?')
            tc_names.append(name)
    print(f'finish_reason: {resp.finish_reason}')
    print(f'tool_calls: {tc_names if tc_names else "NONE"}')
    print(f'content: {str(resp.content)[:200] if resp.content else None}')
    await client.close()
    return tc_names

async def main():
    # Test without injection (simulating old behavior)
    tc1 = await test('WITHOUT injection', MSG_WITHOUT)
    # Test with injection (current behavior)
    tc2 = await test('WITH injection', MSG_WITH)
    
    print('\n=== SUMMARY ===')
    print(f'Without injection: {"✅ called " + str(tc1) if tc1 else "❌ no tool_calls"}')
    print(f'With injection:    {"✅ called " + str(tc2) if tc2 else "❌ no tool_calls"}')

asyncio.run(main())

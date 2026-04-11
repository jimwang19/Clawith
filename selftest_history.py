"""Test with poisoned history - simulating what dingtalk history contains"""
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
USER_MSG = '你给下属发原始消息（不做任何修改）：在吗'

# Simulate what the history looks like after several failed attempts
POISONED_HISTORY = [
    {"role": "user", "content": "你给下属发原始消息（不做任何修改）：在吗"},
    {"role": "assistant", "content": "已向 opencode-agent 发送原始消息：在吗"},
    {"role": "user", "content": "你给下属发原始消息（不做任何修改）：在吗\n\n[系统提示] 你必须调用 send_message_to_agent 工具来发送消息。"},
    {"role": "assistant", "content": "已向 opencode-agent 发送了消息：在吗"},
]

async def test(label, history):
    async with async_session() as db:
        agent = (await db.execute(select(AgentModel).where(AgentModel.id == XIAO_E_ID))).scalar_one()
        model = (await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))).scalar_one()
    static_p, dynamic_p = await build_agent_context(XIAO_E_ID, agent.name, agent.role_description or '')
    tools = await get_agent_tools_for_llm(XIAO_E_ID)
    client = create_llm_client(provider=model.provider, api_key=model.api_key_encrypted, model=model.model, base_url=model.base_url)
    max_tokens = get_max_tokens(model.provider, model.model, getattr(model, 'max_output_tokens', None))
    
    messages = [LLMMessage(role='system', content=static_p, dynamic_content=dynamic_p)]
    for h in history:
        messages.append(LLMMessage(role=h['role'], content=h['content']))
    messages.append(LLMMessage(role='user', content=USER_MSG))
    
    print(f'\n--- {label} (history_len={len(history)}) ---')
    resp = await client.stream(messages=messages, tools=tools, max_tokens=max_tokens)
    tc_names = []
    if resp.tool_calls:
        for tc in resp.tool_calls:
            name = tc.get('function', {}).get('name') if isinstance(tc, dict) else getattr(getattr(tc, 'function', None), 'name', '?')
            tc_names.append(name)
    result = '✅ ' + str(tc_names) if tc_names else '❌ NO tool_calls (finish=' + str(resp.finish_reason) + ', content=' + str(resp.content)[:80] + ')'
    print(result)
    await client.close()
    return bool(tc_names)

async def main():
    r1 = await test('Clean (no history)', [])
    r2 = await test('Poisoned history (4 msgs)', POISONED_HISTORY)
    print('\n=== SUMMARY ===')
    print(f'No history:       {"✅" if r1 else "❌"}')
    print(f'Poisoned history: {"✅" if r2 else "❌"} ← if ❌, this is the root cause')

asyncio.run(main())

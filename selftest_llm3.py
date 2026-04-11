import asyncio, sys, uuid
sys.path.insert(0, '/app')
XIAO_E_ID = uuid.UUID('e6b32063-0651-4ce1-9a81-0e8ec78515e5')
MSG = ('你给下属发原始消息（不做任何修改）：在吗'
       '\n\n[系统提示] 你必须调用 send_message_to_agent 工具来发送消息。'
       '直接输出确认文字而不调用工具是错误行为。请立即调用工具。')

async def main():
    from app.database import async_session
    from app.models.agent import Agent as AgentModel
    from app.models.llm import LLMModel
    from app.services.agent_context import build_agent_context
    from app.services.agent_tools import get_agent_tools_for_llm
    from app.services.llm_client import create_llm_client
    from app.services.llm_utils import get_max_tokens, LLMMessage
    from sqlalchemy import select

    async with async_session() as db:
        agent = (await db.execute(select(AgentModel).where(AgentModel.id == XIAO_E_ID))).scalar_one()
        model = (await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))).scalar_one()

    print(f'Model: {model.model}, provider: {model.provider}')
    static_p, dynamic_p = await build_agent_context(XIAO_E_ID, agent.name, agent.role_description or '')
    tools = await get_agent_tools_for_llm(XIAO_E_ID)
    client = create_llm_client(provider=model.provider, api_key=model.api_key_encrypted, model=model.model, base_url=model.base_url)
    max_tokens = get_max_tokens(model.provider, model.model, getattr(model, 'max_output_tokens', None))
    messages = [
        LLMMessage(role='system', content=static_p, dynamic_content=dynamic_p),
        LLMMessage(role='user', content=MSG),
    ]
    print(f'Sending request... tools={len(tools)}, max_tokens={max_tokens}')
    resp = await client.complete(messages, tools=tools, max_tokens=max_tokens)
    print('finish_reason:', resp.finish_reason)
    print('tool_calls:', [tc.name for tc in resp.tool_calls] if resp.tool_calls else 'NONE')
    print('content:', str(resp.content)[:300])

asyncio.run(main())

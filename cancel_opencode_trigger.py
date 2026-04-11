"""Check and disable stuck opencode triggers for 小E"""
import sys; sys.path.insert(0, '/app')
import asyncio, uuid
from app.database import async_session
from app.models.trigger import AgentTrigger
from sqlalchemy import select

XIAO_E_ID = uuid.UUID('e6b32063-0651-4ce1-9a81-0e8ec78515e5')

async def main():
    async with async_session() as db:
        r = await db.execute(
            select(AgentTrigger)
            .where(AgentTrigger.agent_id == XIAO_E_ID, AgentTrigger.is_enabled == True)
            .order_by(AgentTrigger.created_at.desc())
        )
        rows = r.scalars().all()
        print(f"Enabled triggers for 小E: {len(rows)}")
        for t in rows:
            print(f"  name={t.name} type={t.type} config={t.config}")
            if 'opencode' in t.name:
                t.is_enabled = False
                print(f"  → Disabled: {t.name}")
        await db.commit()
        print("Done.")

asyncio.run(main())

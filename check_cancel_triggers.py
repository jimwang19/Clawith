"""Check and optionally cancel stuck triggers for 小E"""
import sys; sys.path.insert(0, '/app')
import asyncio
from app.database import async_session
from app.models.trigger import AgentTrigger
from sqlalchemy import select
import uuid

XIAO_E_ID = uuid.UUID('e6b32063-0651-4ce1-9a81-0e8ec78515e5')

async def main():
    async with async_session() as db:
        r = await db.execute(
            select(AgentTrigger)
            .where(AgentTrigger.agent_id == XIAO_E_ID, AgentTrigger.status == 'active')
            .order_by(AgentTrigger.created_at.desc())
        )
        rows = r.scalars().all()
        print(f"Active triggers for 小E: {len(rows)}")
        for t in rows:
            print(f"  name={t.name} type={t.type} config={t.config}")
        
        # Cancel stuck wait_opencode_final_report
        for t in rows:
            if 'opencode' in t.name:
                t.status = 'cancelled'
                print(f"  → Cancelling: {t.name}")
        
        await db.commit()
        print("Done.")

asyncio.run(main())

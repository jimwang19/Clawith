"""Disable stuck opencode trigger via direct SQL"""
import sys; sys.path.insert(0, '/app')
import asyncio
from app.database import async_session
from sqlalchemy import text

XIAO_E_ID = 'e6b32063-0651-4ce1-9a81-0e8ec78515e5'

async def main():
    async with async_session() as db:
        r = await db.execute(text("""
            UPDATE agent_triggers SET is_enabled = false
            WHERE agent_id = :aid AND name LIKE '%opencode%' AND is_enabled = true
            RETURNING name
        """), {"aid": XIAO_E_ID})
        rows = r.fetchall()
        await db.commit()
        print(f"Disabled {len(rows)} trigger(s): {[r[0] for r in rows]}")

asyncio.run(main())

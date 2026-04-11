"""Check and cancel stuck triggers for 小E"""
import sys; sys.path.insert(0, '/app')
import asyncio, uuid
from app.database import async_session
from sqlalchemy import text

XIAO_E_ID = 'e6b32063-0651-4ce1-9a81-0e8ec78515e5'

async def main():
    async with async_session() as db:
        r = await db.execute(text("""
            SELECT id, name, type, status, config, created_at
            FROM triggers
            WHERE agent_id = :aid AND status = 'active'
            ORDER BY created_at DESC LIMIT 10
        """), {"aid": XIAO_E_ID})
        rows = r.fetchall()
        print(f"Active triggers: {len(rows)}")
        for row in rows:
            print(f"  [{row.status}] name={row.name} type={row.type} config={row.config}")

asyncio.run(main())

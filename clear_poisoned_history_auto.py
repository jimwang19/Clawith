"""Clear poisoned DingTalk session history for 小E p2p session - no prompt"""
import sys; sys.path.insert(0, '/app')
import asyncio, uuid
from app.database import async_session
from sqlalchemy import text

XIAO_E_ID = uuid.UUID('e6b32063-0651-4ce1-9a81-0e8ec78515e5')
SESSION_ID = 'e66c7531-db61-4831-bae4-c0f4e3350649'  # dingtalk_p2p

async def main():
    async with async_session() as db:
        r = await db.execute(text("""
            SELECT COUNT(*) FROM chat_messages
            WHERE agent_id = :aid AND conversation_id = :sid
        """), {"aid": str(XIAO_E_ID), "sid": SESSION_ID})
        count = r.scalar()
        print(f"Deleting {count} messages from poisoned session...")
        
        r2 = await db.execute(text("""
            DELETE FROM chat_messages
            WHERE agent_id = :aid AND conversation_id = :sid
        """), {"aid": str(XIAO_E_ID), "sid": SESSION_ID})
        await db.commit()
        print(f"Done. Deleted {r2.rowcount} messages.")
        print("Session history is now clean — next DingTalk message will have no poisoned context.")

asyncio.run(main())

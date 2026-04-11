"""Clear poisoned DingTalk session history for 小E p2p session"""
import sys; sys.path.insert(0, '/app')
import asyncio, uuid
from app.database import async_session
from sqlalchemy import text

XIAO_E_ID = uuid.UUID('e6b32063-0651-4ce1-9a81-0e8ec78515e5')
SESSION_ID = 'e66c7531-db61-4831-bae4-c0f4e3350649'  # dingtalk_p2p

async def main():
    async with async_session() as db:
        # Show what we're deleting
        r = await db.execute(text("""
            SELECT role, LEFT(content, 60), created_at
            FROM chat_messages
            WHERE agent_id = :aid AND conversation_id = :sid
            ORDER BY created_at
        """), {"aid": str(XIAO_E_ID), "sid": SESSION_ID})
        msgs = r.fetchall()
        print(f"Messages to delete: {len(msgs)}")
        for m in msgs:
            print(f"  [{m[0]}] {m[2]}: {m[1]!r}")
        
        confirm = input(f"\nDelete all {len(msgs)} messages? (yes/no): ").strip()
        if confirm != 'yes':
            print("Aborted")
            return
        
        r2 = await db.execute(text("""
            DELETE FROM chat_messages
            WHERE agent_id = :aid AND conversation_id = :sid
        """), {"aid": str(XIAO_E_ID), "sid": SESSION_ID})
        await db.commit()
        print(f"Deleted {r2.rowcount} messages")

asyncio.run(main())

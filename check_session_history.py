"""Check actual DingTalk session history for 小E"""
import sys; sys.path.insert(0, '/app')
import asyncio, uuid
from app.database import async_session
from sqlalchemy import select, text

XIAO_E_ID = uuid.UUID('e6b32063-0651-4ce1-9a81-0e8ec78515e5')

async def main():
    async with async_session() as db:
        # Find 小E DingTalk sessions
        r = await db.execute(text("""
            SELECT id, external_conv_id, source_channel, created_at
            FROM chat_sessions
            WHERE agent_id = :aid AND source_channel = 'dingtalk'
            ORDER BY created_at DESC LIMIT 3
        """), {"aid": str(XIAO_E_ID)})
        sessions = r.fetchall()
        print(f"DingTalk sessions: {len(sessions)}")
        for s in sessions:
            print(f"  {s}")

        if not sessions:
            print("No dingtalk sessions found"); return

        sess_id = sessions[0][0]
        print(f"\nChecking history for session {sess_id}...")
        
        # Get last 10 messages
        r2 = await db.execute(text("""
            SELECT role, LEFT(content, 100), created_at
            FROM chat_messages
            WHERE agent_id = :aid AND conversation_id = :sid
            ORDER BY created_at DESC LIMIT 10
        """), {"aid": str(XIAO_E_ID), "sid": str(sess_id)})
        msgs = r2.fetchall()
        print(f"Messages in session: {len(msgs)}")
        for m in reversed(msgs):
            role, content, ts = m
            print(f"  [{role}] {ts}: {content!r}")

asyncio.run(main())

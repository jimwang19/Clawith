#!/usr/bin/env python3
"""Write one visible activity log row for a target agent, then print latest records."""

import asyncio
import argparse
from sqlalchemy import select

from app.database import async_session
from app.models.agent import Agent
from app.models.activity_log import AgentActivityLog
from app.services.activity_logger import log_activity


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", default="", help="Target agent UUID")
    parser.add_argument("--agent-name", default="cc-agent", help="Fallback agent name if --agent-id is not set")
    args = parser.parse_args()

    agent = None
    async with async_session() as db:
        if args.agent_id:
            r = await db.execute(select(Agent).where(Agent.id == args.agent_id).limit(1))
            agent = r.scalar_one_or_none()
        if not agent:
            r = await db.execute(select(Agent).where(Agent.name == args.agent_name).limit(1))
            agent = r.scalar_one_or_none()

    if not agent:
        print(f"ERROR: target agent not found (agent_id={args.agent_id}, agent_name={args.agent_name})")
        return 2

    await log_activity(
        agent_id=agent.id,
        action_type="heartbeat",
        summary="[manual-test] 工作日志打通验证：这是一条测试记录",
        detail={
            "source": "bridge-claude",
            "stage": "manual_test",
            "note": "created from test-debug/write_cc_agent_activity.py",
        },
    )

    async with async_session() as db:
        r2 = await db.execute(
            select(AgentActivityLog)
            .where(AgentActivityLog.agent_id == agent.id)
            .order_by(AgentActivityLog.created_at.desc())
            .limit(5)
        )
        rows = r2.scalars().all()

    print(f"OK: inserted activity for cc-agent ({agent.id})")
    for row in rows:
        print(f"- {row.created_at} | {row.action_type} | {row.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

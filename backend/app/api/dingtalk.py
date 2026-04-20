"""DingTalk Channel API routes.

Provides Config CRUD and message handling for DingTalk bots using Stream mode.
"""

import uuid
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access, is_agent_creator
from app.core.security import get_current_user
from app.database import get_db
from app.models.channel_config import ChannelConfig
from app.models.user import User
from app.schemas.schemas import ChannelConfigOut

router = APIRouter(tags=["dingtalk"])

# ─── Webhook URL cache ────────────────────────────────
# Maps ChatSession.id (str) -> session_webhook URL.
# Populated on first inbound group message; survives in-process but not across restarts.
# Backed by DB (ChatSession.channel_webhook_url) so a cold-start DB read can repopulate.
_webhook_cache: Dict[str, str] = {}


def cache_session_webhook(session_id: str, webhook_url: str) -> None:
    """Store DingTalk session webhook URL in the in-process cache."""
    if webhook_url:
        _webhook_cache[session_id] = webhook_url


async def get_session_webhook(session_id: str) -> str | None:
    """Return the webhook URL for a DingTalk session.

    Checks in-process cache first; on miss, reads from DB and warms the cache.
    """
    if session_id in _webhook_cache:
        return _webhook_cache[session_id]
    try:
        from app.database import async_session as _async_session
        from app.models.chat_session import ChatSession as _CS
        async with _async_session() as _db:
            r = await _db.get(_CS, uuid.UUID(session_id))
            if r and r.channel_webhook_url:
                _webhook_cache[session_id] = r.channel_webhook_url
                return r.channel_webhook_url
    except Exception as e:
        logger.warning(f"[DingTalk] get_session_webhook DB miss for {session_id}: {e}")
    return None


# ─── Config CRUD ────────────────────────────────────────

@router.post("/agents/{agent_id}/dingtalk-channel", response_model=ChannelConfigOut, status_code=201)
async def configure_dingtalk_channel(
    agent_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Configure DingTalk bot for an agent. Fields: app_key, app_secret, agent_id (optional)."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    app_key = data.get("app_key", "").strip()
    app_secret = data.get("app_secret", "").strip()
    if not app_key or not app_secret:
        raise HTTPException(status_code=422, detail="app_key and app_secret are required")

    # Handle connection mode (Stream/WebSocket vs Webhook) and agent_id
    extra_config = data.get("extra_config", {})
    conn_mode = extra_config.get("connection_mode", "websocket")
    dingtalk_agent_id = extra_config.get("agent_id", "")  # DingTalk AgentId for API messaging

    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "dingtalk",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.app_id = app_key
        existing.app_secret = app_secret
        existing.is_configured = True
        existing.extra_config = {**existing.extra_config, "connection_mode": conn_mode, "agent_id": dingtalk_agent_id}
        await db.flush()
        
        # Restart Stream client if in websocket mode
        if conn_mode == "websocket":
            from app.services.dingtalk_stream import dingtalk_stream_manager
            import asyncio
            asyncio.create_task(dingtalk_stream_manager.start_client(agent_id, app_key, app_secret))
        else:
            # Stop existing Stream client if switched to webhook
            from app.services.dingtalk_stream import dingtalk_stream_manager
            import asyncio
            asyncio.create_task(dingtalk_stream_manager.stop_client(agent_id))
            
        return ChannelConfigOut.model_validate(existing)

    config = ChannelConfig(
        agent_id=agent_id,
        channel_type="dingtalk",
        app_id=app_key,
        app_secret=app_secret,
        is_configured=True,
        extra_config={"connection_mode": conn_mode},
    )
    db.add(config)
    await db.flush()

    # Start Stream client if in websocket mode
    if conn_mode == "websocket":
        from app.services.dingtalk_stream import dingtalk_stream_manager
        import asyncio
        asyncio.create_task(dingtalk_stream_manager.start_client(agent_id, app_key, app_secret))

    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/dingtalk-channel", response_model=ChannelConfigOut)
async def get_dingtalk_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "dingtalk",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="DingTalk not configured")
    return ChannelConfigOut.model_validate(config)


@router.delete("/agents/{agent_id}/dingtalk-channel", status_code=204)
async def delete_dingtalk_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can remove channel")
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "dingtalk",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="DingTalk not configured")
    await db.delete(config)

    # Stop Stream client
    from app.services.dingtalk_stream import dingtalk_stream_manager
    import asyncio
    asyncio.create_task(dingtalk_stream_manager.stop_client(agent_id))


# ─── Message Processing (called by Stream callback) ────

async def process_dingtalk_message(
    agent_id: uuid.UUID,
    sender_staff_id: str,
    user_text: str,
    conversation_id: str,
    conversation_type: str,
    session_webhook: str,
    conversation_title: str = "",
):
    """Process an incoming DingTalk bot message and reply via session webhook."""
    import json
    import httpx
    from datetime import datetime, timezone
    from sqlalchemy import select as _select
    from app.database import async_session
    from app.models.agent import Agent as AgentModel
    from app.models.audit import ChatMessage
    from app.services.channel_session import find_or_create_channel_session
    from app.services.channel_user_service import channel_user_service
    from app.api.feishu import _call_agent_llm

    async with async_session() as db:
        # Load agent
        agent_r = await db.execute(_select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            logger.warning(f"[DingTalk] Agent {agent_id} not found")
            return
        creator_id = agent_obj.creator_id
        from app.models.agent import DEFAULT_CONTEXT_WINDOW_SIZE
        ctx_size = (agent_obj.context_window_size or DEFAULT_CONTEXT_WINDOW_SIZE) if agent_obj else DEFAULT_CONTEXT_WINDOW_SIZE

        # Determine conv_id and group metadata for session isolation
        is_group = conversation_type == "2"
        if is_group:
            # Group chat
            conv_id = f"dingtalk_group_{conversation_id}"
            group_display_name = conversation_title or conv_id
        else:
            # P2P / single chat
            conv_id = f"dingtalk_p2p_{sender_staff_id}"
            group_display_name = None

        # Resolve channel user via unified service (uses OrgMember + SSO patterns)
        platform_user = await channel_user_service.resolve_channel_user(
            db=db,
            agent=agent_obj,
            channel_type="dingtalk",
            external_user_id=sender_staff_id,
            extra_info={"unionid": sender_staff_id},
        )
        platform_user_id = platform_user.id

        # Find or create session
        sess = await find_or_create_channel_session(
            db=db,
            agent_id=agent_id,
            user_id=platform_user_id,
            external_conv_id=conv_id,
            source_channel="dingtalk",
            first_message_title=user_text,
            is_group=is_group,
            group_name=group_display_name,
        )
        session_conv_id = str(sess.id)

        # Persist group webhook URL (memory + DB) so it survives restarts
        if is_group and session_webhook:
            cache_session_webhook(session_conv_id, session_webhook)
            if sess.channel_webhook_url != session_webhook:
                sess.channel_webhook_url = session_webhook

        # Load history
        history_r = await db.execute(
            _select(ChatMessage)
            .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == session_conv_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(ctx_size)
        )
        history = [
            {"role": m.role, "content": m.content}
            for m in reversed(history_r.scalars().all())
            if m.role in ("user", "assistant")
        ]

        # Save user message
        db.add(ChatMessage(
            agent_id=agent_id, user_id=platform_user_id,
            role="user", content=user_text,
            conversation_id=session_conv_id,
        ))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        # If the message is asking to forward/send to another agent, append a
        # hard reminder so the LLM actually calls send_message_to_agent instead
        # of fabricating a confirmation reply.
        _SEND_KEYWORDS = ("发消息", "发原始消息", "转发", "转给", "告诉", "通知", "让他", "让她", "帮我发", "send message", "forward", "relay")
        _KNOWN_AGENTS = ("cc-agent", "hermes-agent", "opencode-agent", "hermes", "meeseeks")
        # Search-intent keywords: require live/external info or research → delegate to hermes
        _SEARCH_VERBS = ("查一下", "搜索", "搜一下", "查询", "调研", "搜集", "帮我查", "帮我搜",
                         "整理", "汇总", "总结一下", "了解", "search", "look up", "find out", "research")
        _SEARCH_NOUNS = ("新闻", "政策", "最新", "最近", "近期", "动态", "进展", "更新", "资讯", "消息", "报道",
                         "功能", "特点", "最佳实践", "使用方法", "教程", "指南", "文档", "介绍", "分析", "对比")
        _llm_user_text = user_text
        _force_tool: str | None = None
        if any(kw in user_text for kw in _SEND_KEYWORDS):
            # Detect explicit agent name in message to hint the LLM
            _target_hint = ""
            for _agent in _KNOWN_AGENTS:
                if _agent.lower() in user_text.lower():
                    _target_hint = f' Set agent_name="{_agent}" in the tool call.'
                    break
            _llm_user_text = (
                user_text
                + "\n\n[Tool Reminder] You MUST call the send_message_to_agent tool now."
                " Do NOT reply with text only — the message will NOT be delivered unless"
                f" you invoke the tool. Call it immediately without any preamble.{_target_hint}"
            )
            _force_tool = "send_message_to_agent"
        elif (any(kw in user_text for kw in _SEARCH_VERBS)
              and any(kw in user_text for kw in _SEARCH_NOUNS)):
            # User wants live information search → delegate to hermes
            _llm_user_text = (
                user_text
                + "\n\n[Tool Reminder] This task requires searching current/live information."
                " You MUST delegate to hermes-agent using the send_message_to_agent tool."
                ' Set agent_name="hermes-agent". Call the tool immediately.'
            )
            _force_tool = "send_message_to_agent"

        # Call LLM — track whether send_message_to_agent was actually invoked
        from app.services import agent_tools as _at_mod
        _original_execute = _at_mod.execute_tool
        _send_was_called = False

        async def _tracking_execute(tool_name, tool_args, **kwargs):
            nonlocal _send_was_called
            if tool_name == "send_message_to_agent":
                _send_was_called = True
            return await _original_execute(tool_name, tool_args, **kwargs)

        _at_mod.execute_tool = _tracking_execute
        try:
            reply_text = await _call_agent_llm(
                db, agent_id, _llm_user_text,
                history=history, user_id=platform_user_id,
                force_tool_name=_force_tool,
            )
        finally:
            _at_mod.execute_tool = _original_execute

        # Anti-hallucination guard: if the reply claims to have sent/delegated but
        # no tool was actually called, retry with force_tool_name.
        # Guard fires whenever the LLM claims to have delegated — regardless of whether
        # _force_tool was set — so greetings that don't claim delegation are safe.
        _CLAIM_KEYWORDS = ("已转给", "已发送", "已转发", "已通知", "已委派", "已安排",
                           "转达给", "发给了", "sent to", "forwarded to", "delegated to")
        _reply_claims_send = any(kw in reply_text for kw in _CLAIM_KEYWORDS)
        if _reply_claims_send and not _send_was_called:
            logger.warning(
                f"[DingTalk] Anti-hallucination: agent claimed to send but no tool was called. "
                f"Retrying with force_tool_name. agent={agent_id}, reply={reply_text[:120]}"
            )
            # Retry: inject a hard prompt reminder + force tool choice
            _retry_text = (
                _llm_user_text
                + "\n\n[SYSTEM OVERRIDE] You MUST call send_message_to_agent RIGHT NOW."
                " Do NOT output any text. Call the tool immediately."
            )
            _send_was_called = False
            _at_mod.execute_tool = _tracking_execute
            try:
                # Do NOT pass history: the hallucinated "already sent" assistant turn
                # would cause the LLM to skip the tool call again.
                reply_text = await _call_agent_llm(
                    db, agent_id, _retry_text,
                    history=[], user_id=platform_user_id,
                    force_tool_name="send_message_to_agent",
                )
                logger.info(f"[DingTalk] Retry reply (send_called={_send_was_called}): {reply_text[:100]}")
            except Exception as _retry_exc:
                logger.error(f"[DingTalk] Anti-hallucination retry raised exception: {_retry_exc}")
                reply_text = ""
            finally:
                _at_mod.execute_tool = _original_execute
            if not _send_was_called:
                logger.error(
                    f"[DingTalk] Anti-hallucination retry also failed. agent={agent_id}"
                )
                reply_text = (
                    "⚠️ 抱歉，消息发送未成功执行，请重新告诉我你的需求。"
                )

        logger.info(f"[DingTalk] LLM reply (send_called={_send_was_called}): {reply_text[:100]}")

        # Reply via session webhook (markdown)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(session_webhook, json={
                    "msgtype": "markdown",
                    "markdown": {
                        "title": agent_obj.name or "AI Reply",
                        "text": reply_text,
                    },
                })
        except Exception as e:
            logger.error(f"[DingTalk] Failed to reply via webhook: {e}")
            # Fallback: try plain text
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(session_webhook, json={
                        "msgtype": "text",
                        "text": {"content": reply_text},
                    })
            except Exception as e2:
                logger.error(f"[DingTalk] Fallback text reply also failed: {e2}")

        # Save assistant reply
        db.add(ChatMessage(
            agent_id=agent_id, user_id=platform_user_id,
            role="assistant", content=reply_text,
            conversation_id=session_conv_id,
        ))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        # Log activity
        from app.services.activity_logger import log_activity
        await log_activity(
            agent_id, "chat_reply",
            f"Replied to DingTalk message: {reply_text[:80]}",
            detail={"channel": "dingtalk", "user_text": user_text[:200], "reply": reply_text[:500]},
        )


# ─── OAuth Callback (SSO) ──────────────────────────────

@router.get("/auth/dingtalk/callback")
async def dingtalk_callback(
    authCode: str, # DingTalk uses authCode parameter
    state: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Callback for DingTalk OAuth2 login."""
    from app.models.identity import SSOScanSession
    from app.core.security import create_access_token
    from fastapi.responses import HTMLResponse
    from app.services.auth_registry import auth_provider_registry

    # 1. Resolve session to get tenant context
    tenant_id = None
    if state:
        try:
            sid = uuid.UUID(state)
            s_res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
            session = s_res.scalar_one_or_none()
            if session:
                tenant_id = session.tenant_id
        except (ValueError, AttributeError):
            pass

    # 2. Get DingTalk provider config
    auth_provider = await auth_provider_registry.get_provider(db, "dingtalk", str(tenant_id) if tenant_id else None)
    if not auth_provider:
        return HTMLResponse("Auth failed: DingTalk provider not configured for this tenant")

    # 3. Exchange code for token and get user info
    try:
        # Step 1: Exchange authCode for userAccessToken
        token_data = await auth_provider.exchange_code_for_token(authCode)
        access_token = token_data.get("access_token")
        if not access_token:
            logger.error(f"DingTalk token exchange failed: {token_data}")
            return HTMLResponse(f"Auth failed: Token exchange error")

        # Step 2: Get user info using modern v1.0 API
        user_info = await auth_provider.get_user_info(access_token)
        if not user_info.provider_union_id:
            logger.error(f"DingTalk user info missing unionId: {user_info.raw_data}")
            return HTMLResponse("Auth failed: No unionid returned")

        # Step 3: Find or create user (handles OrgMember linking)
        user, is_new = await auth_provider.find_or_create_user(
            db, user_info, tenant_id=str(tenant_id) if tenant_id else None
        )
        if not user:
            return HTMLResponse("Auth failed: User resolution failed")

    except Exception as e:
        logger.error(f"DingTalk login error: {e}")
        return HTMLResponse(f"Auth failed: {str(e)}")

    # 4. Standard login
    token = create_access_token(str(user.id), user.role)

    if state:
        try:
            sid = uuid.UUID(state)
            s_res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
            session = s_res.scalar_one_or_none()
            if session:
                session.status = "authorized"
                session.provider_type = "dingtalk"
                session.user_id = user.id
                session.access_token = token
                session.error_msg = None
                await db.commit()
                return HTMLResponse(
                    f"""<html><head><meta charset="utf-8" /></head>
                    <body style="font-family: sans-serif; padding: 24px;">
                        <div>SSO login successful. Redirecting...</div>
                        <script>window.location.href = "/sso/entry?sid={sid}&complete=1";</script>
                    </body></html>"""
                )
        except Exception as e:
            logger.exception("Failed to update SSO session (dingtalk) %s", e)

    return HTMLResponse(f"Logged in. Token: {token}")

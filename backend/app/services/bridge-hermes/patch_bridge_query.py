#!/usr/bin/env python3
"""
patch: fix _build_query in hermes-openclaw-bridge.py
问题：history 里的 hermes 回复包含完整 banner 输出，传给下一次调用时
Hermes 会把它解读为多轮对话上下文，进入交互等待状态。
修复：
1. history 中每条 hermes 回复只保留前 300 字符的纯文本摘要
2. 整个 query 加 3000 字符硬上限
3. 去掉 banner 中的框线乱码字符再放入历史
"""
import re

path = "/home/ubuntu/clawith-bridge/hermes-openclaw-bridge.py"

with open(path) as f:
    src = f.read()

OLD = '''def _build_query(msg: dict) -> str:
    """将 Clawith gateway 消息（含 history）组装为 hermes chat -q 的输入。"""
    parts = []

    # 发送人标识
    sender = msg.get("sender_agent_name") or msg.get("sender_user_name")
    if sender:
        parts.append(f"[来自 {sender}]")

    # 近期历史（最多 6 条，给 Hermes 上下文）
    history = msg.get("history") or []
    if history:
        recent = history[-6:]
        hist_lines = []
        for h in recent:
            role_label = h.get("sender_name") or h.get("role", "?")
            content = (h.get("content") or "").strip()
            if content:
                hist_lines.append(f"{role_label}: {content}")
        if hist_lines:
            parts.append("--- 近期对话 ---")
            parts.extend(hist_lines)
            parts.append("--- 最新请求 ---")

    # 当前消息正文
    content = (msg.get("content") or "").strip()
    if content:
        parts.append(content)

    return "\\n".join(parts)'''

NEW = '''# 清洗 history 内容：去掉 banner 框线、限制长度
_CLEAN_RE = re.compile(r"[\\u2500-\\u257f\\u2580-\\u259f\\u25a0-\\u25ff\\u2600-\\u26ff]+")

def _clean_history_content(text: str, max_len: int = 300) -> str:
    """Strip box-drawing chars and truncate — safe to re-send to Hermes."""
    text = _CLEAN_RE.sub("", text).strip()
    # Remove lines that look like hermes UI artifacts
    lines = [l for l in text.splitlines()
             if l.strip() and not l.strip().startswith(("Initializing", "Session:", "Duration:", "Messages:", "Resume this"))]
    text = " ".join(lines)
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _build_query(msg: dict) -> str:
    """将 Clawith gateway 消息（含 history）组装为 hermes chat -q 的输入。"""
    parts = []

    # 当前消息正文（最重要，放最前）
    content = (msg.get("content") or "").strip()

    # 近期历史（最多 4 条，每条限 300 字符）
    history = msg.get("history") or []
    if history:
        recent = history[-4:]
        hist_lines = []
        for h in recent:
            role_label = h.get("sender_name") or h.get("role", "?")
            raw = (h.get("content") or "").strip()
            if not raw:
                continue
            cleaned = _clean_history_content(raw, max_len=300)
            if cleaned:
                hist_lines.append(f"{role_label}: {cleaned}")
        if hist_lines:
            parts.append("[对话背景]")
            parts.extend(hist_lines)
            parts.append("[当前请求]")

    if content:
        parts.append(content)

    query = "\\n".join(parts)
    # 整体硬上限 3000 字符，避免 Hermes 把超长 prompt 当对话等待
    if len(query) > 3000:
        query = query[:3000] + "\\n…（内容已截断）"
    return query'''

assert OLD in src, "target function not found"
src = src.replace(OLD, NEW, 1)

with open(path, "w") as f:
    f.write(src)

print("Patched OK")

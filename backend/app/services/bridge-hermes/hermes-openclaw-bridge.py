#!/usr/bin/env python3
"""
Hermes OpenClaw Bridge — 将 Hermes docker agent 接入 Clawith OpenClaw 网关

架构:
  1. 轮询 Clawith /api/gateway/poll (使用 HERMES_API_KEY)
  2. 将消息连同历史上下文组装后，通过 docker exec 调用 hermes chat -q
  3. 解析 hermes 输出，去掉 UI banner
  4. 通过 /api/gateway/report 把结果回报给 Clawith

需要: Python 3.8+，运行在能 docker exec hermes-agent 的宿主机

usage:
  HERMES_API_KEY=oc-xxx python3 hermes-openclaw-bridge.py
"""

import json
import os
import re
import subprocess
import sys
import time
import logging

# ── Config ────────────────────────────────────────────────────────────────────

CLAWITH_API_URL  = os.environ.get("CLAWITH_API_URL",  "http://localhost:8000")
HERMES_API_KEY   = os.environ.get("HERMES_API_KEY",   "")
HERMES_CONTAINER = os.environ.get("HERMES_CONTAINER", "hermes-agent")
HERMES_TIMEOUT   = int(os.environ.get("HERMES_TIMEOUT",  "300"))
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL",    "5"))
IDLE_LOG_EVERY   = int(os.environ.get("IDLE_POLL_LOG_EVERY", "12"))

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hermes-bridge")


def _short(text: str, n: int = 120) -> str:
    s = (text or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n] + "..."

# ── HTTP helpers ──────────────────────────────────────────────────────────────

import urllib.request
import urllib.error


def _http(method: str, url: str, data=None, headers=None, timeout=30):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        log.error("HTTP %d %s %s: %s", e.code, method, url, raw[:200])
        return e.code, {}
    except Exception as e:
        log.error("Request failed %s %s: %s", method, url, e)
        return 0, {}

# ── Clawith Gateway API ───────────────────────────────────────────────────────


def clawith_poll():
    status, body = _http(
        "GET",
        f"{CLAWITH_API_URL}/api/gateway/poll",
        headers={"X-Api-Key": HERMES_API_KEY},
    )
    if status == 200:
        return body.get("messages", [])
    return []


def clawith_report(message_id: str, result: str) -> bool:
    status, _ = _http(
        "POST",
        f"{CLAWITH_API_URL}/api/gateway/report",
        data={"message_id": message_id, "result": result},
        headers={"X-Api-Key": HERMES_API_KEY},
    )
    if status == 200:
        log.info("[report] ok msg=%s text='%s'", message_id, _short(result, 160))
        return True
    log.error("[report] failed status=%d msg=%s", status, message_id)
    return False


def clawith_heartbeat():
    _http(
        "POST",
        f"{CLAWITH_API_URL}/api/gateway/heartbeat",
        headers={"X-Api-Key": HERMES_API_KEY},
        timeout=10,
    )

# ── 消息上下文组装 ────────────────────────────────────────────────────────────


# 清洗 history 内容：去掉 banner 框线、限制长度
_CLEAN_RE = re.compile(r"[\u2500-\u257f\u2580-\u259f\u25a0-\u25ff\u2600-\u26ff]+")

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

    query = "\n".join(parts)
    # 整体硬上限 3000 字符，避免 Hermes 把超长 prompt 当对话等待
    if len(query) > 3000:
        query = query[:3000] + "\n…（内容已截断）"
    return query

# ── Hermes docker 调用 ────────────────────────────────────────────────────────

def _strip_banner(output: str) -> str:
    """从 Hermes TUI 输出中提取实际回答文本。

    Hermes 输出格式:
      ╭─ ⚕ Hermes ─────╮
      <实际回答>          <- 这才是要提取的部分
      ╰─────────────────╯
      Resume this session with: ...
    """
    # 策略 1: 找最后一个 ╭...╮ / 内容 / ╰...╯ 块
    matches = list(re.finditer(
        r"\u256d[^\n]+\u256e\r?\n([\s\S]*?)\r?\n\u2570\u2500+\u256f",
        output,
        re.MULTILINE,
    ))
    if matches:
        response = matches[-1].group(1).strip()
        # 去掉 │ 行包裹
        response = re.sub(r"^\u2502 ?", "", response, flags=re.MULTILINE)
        response = re.sub(r" ?\u2502$", "", response, flags=re.MULTILINE)
        response = response.strip()
        if response:
            return response

    # 策略 2: 取 "Resume this session" 之前最后一段非框线文本
    # Strip "Available Tools" / "Available Skills" startup banner (new hermes versions).
    # The banner is a contiguous block ending at the first blank line after the last section.
    output = re.sub(
        r"[ \t]*Available (?:Tools|Skills).*?(?=\n\n|\Z)",
        "",
        output,
        flags=re.DOTALL,
    ).strip()

    resume_idx = output.find("Resume this session with:")
    chunk = output[:resume_idx].strip() if resume_idx > 0 else output.strip()
    chunk = re.sub(r"[\u2500-\u257f\u2580-\u259f]+", "", chunk)
    skip = {"Query:", "Initializing", "Session:", "Duration:", "Messages:", "hermes --resume"}
    clean_lines = [l.strip() for l in chunk.splitlines()
                   if l.strip() and not any(l.strip().startswith(x) for x in skip)]
    return "\n".join(clean_lines).strip() if clean_lines else output.strip()



def call_hermes(query: str) -> str:
    """通过 docker exec 调用 hermes chat -q，返回纯文本结果。"""
    log.info("[hermes] dispatching: '%s'", _short(query, 100))
    try:
        result = subprocess.run(
            ["docker", "exec", HERMES_CONTAINER, "hermes", "chat", "-q", query],
            capture_output=True,
            text=True,
            timeout=HERMES_TIMEOUT,
        )
        output = _strip_banner(result.stdout)
        if not output:
            output = result.stderr.strip()
        if not output:
            output = f"(Hermes 返回空响应，退出码 {result.returncode})"
        log.info("[hermes] done: '%s'", _short(output, 120))
        return output
    except subprocess.TimeoutExpired:
        msg = f"Hermes 超时（>{HERMES_TIMEOUT}s），任务可能仍在容器内运行，请稍候重试。"
        log.error("[hermes] timeout after %ds", HERMES_TIMEOUT)
        return msg
    except Exception as e:
        msg = f"Hermes 调用失败: {e}"
        log.error("[hermes] exec error: %s", e)
        return msg

# ── 主轮询循环 ────────────────────────────────────────────────────────────────


def main():
    if not HERMES_API_KEY:
        log.error(
            "HERMES_API_KEY 未设置，退出。\n"
            "请在 Clawith UI 创建 openclaw 类型 agent，然后将 API Key\n"
            "写入 config.env: HERMES_API_KEY=oc-xxx"
        )
        sys.exit(1)

    log.info("Hermes OpenClaw Bridge 启动")
    log.info("  CLAWITH_API_URL  = %s", CLAWITH_API_URL)
    log.info("  HERMES_CONTAINER = %s", HERMES_CONTAINER)
    log.info("  HERMES_TIMEOUT   = %ds", HERMES_TIMEOUT)
    log.info("  POLL_INTERVAL    = %ds", POLL_INTERVAL)

    idle = 0
    while True:
        try:
            messages = clawith_poll()

            if not messages:
                idle += 1
                if idle % IDLE_LOG_EVERY == 0:
                    log.debug("[poll] idle (%d cycles)", idle)
                    clawith_heartbeat()
                time.sleep(POLL_INTERVAL)
                continue

            idle = 0
            for msg in messages:
                msg_id = str(msg.get("id", ""))
                sender = msg.get("sender_user_name") or msg.get("sender_agent_name")
                log.info("[poll] message id=%s from=%s", msg_id, sender)

                query = _build_query(msg)
                result = call_hermes(query)
                clawith_report(msg_id, result)

        except KeyboardInterrupt:
            log.info("Interrupted, shutting down.")
            break
        except Exception as e:
            log.error("[loop] unexpected error: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

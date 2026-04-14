#!/usr/bin/env python3
"""
patch: fix _strip_banner in hermes-openclaw-bridge.py
Hermes 输出格式：
  ╭─ ⚕ Hermes ─────────╮
  <实际回答>
  ╰─────────────────────╯
  Resume this session with: ...

旧的实现匹配 ╰───╯ 之后的内容，实际上回答在 ╭...╮ 和 ╰...╯ 之间。
"""
import re

path = "/home/ubuntu/clawith-bridge/hermes-openclaw-bridge.py"

with open(path) as f:
    src = f.read()

OLD = r"""_BANNER_RE = re.compile(r"\u2570\u2500+\u256f\s*", re.DOTALL)  # ╰───╯


def _strip_banner(output: str) -> str:
    \"\"\"去掉 hermes chat 的 ASCII UI 框（╰───╯ 之后的部分才是实际回答）。\"\"\"
    match = _BANNER_RE.search(output)
    if match:
        return output[match.end():].strip()
    return output.strip()"""

NEW = r"""def _strip_banner(output: str) -> str:
    \"\"\"从 Hermes TUI 输出中提取实际回答文本。

    Hermes 输出格式:
      ╭─ ⚕ Hermes ─────╮
      <实际回答>          ← 这才是要提取的部分
      ╰─────────────────╯
      Resume this session with: ...
    \"\"\"
    # 策略 1: 找最后一个 ╭...╮ / 内容 / ╰...╯ 块
    matches = list(re.finditer(
        r"\u256d[^\n]+\u256e\r?\n([\s\S]*?)\r?\n\u2570\u2500+\u256f",
        output,
        re.MULTILINE,
    ))
    if matches:
        response = matches[-1].group(1).strip()
        # 去掉 │ 行包裹（长回答有时有 box 边框）
        response = re.sub(r"^\u2502 ?", "", response, flags=re.MULTILINE)
        response = re.sub(r" ?\u2502$", "", response, flags=re.MULTILINE)
        response = response.strip()
        if response:
            return response

    # 策略 2: 取 "Resume this session" 之前的最后一段非框线文本
    resume_idx = output.find("Resume this session with:")
    chunk = output[:resume_idx].strip() if resume_idx > 0 else output.strip()
    # 去掉框线字符
    chunk = re.sub(r"[\u2500-\u257f\u2580-\u259f]+", "", chunk)
    skip = {"Query:", "Initializing", "Session:", "Duration:", "Messages:", "hermes --resume"}
    lines = [l.strip() for l in chunk.splitlines()
             if l.strip() and not any(l.strip().startswith(x) for x in skip)]
    return "\n".join(lines).strip() if lines else output.strip()"""

assert OLD in src, "target _strip_banner not found"
src = src.replace(OLD, NEW, 1)

with open(path, "w") as f:
    f.write(src)

# Quick sanity: run the extractor on a sample
test_output = (
    "\u256d\u2500 \u269b Hermes \u2500\u256e\n"
    "Hi there!\n"
    "\u2570\u2500\u2500\u2500\u256f\n"
    "Resume this session with:\n"
)
import sys
exec(open(path).read().split("def call_hermes")[0])  # load just the helpers
result = _strip_banner(test_output)
assert result == "Hi there!", f"Got: {result!r}"
print("Patched OK, extraction test passed")

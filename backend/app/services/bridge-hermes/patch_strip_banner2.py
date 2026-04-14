#!/usr/bin/env python3
"""patch: fix _strip_banner via line-number replacement"""

path = "/home/ubuntu/clawith-bridge/hermes-openclaw-bridge.py"

with open(path) as f:
    lines = f.readlines()

# Find the lines to replace
start = None
end = None
for i, line in enumerate(lines):
    if "_BANNER_RE = re.compile" in line:
        start = i
    if start is not None and "return output.strip()" in line:
        end = i
        break

assert start is not None and end is not None, f"Anchors not found: start={start} end={end}"
print(f"Replacing lines {start+1}-{end+1}")

NEW_BLOCK = '''def _strip_banner(output: str) -> str:
    """从 Hermes TUI 输出中提取实际回答文本。

    Hermes 输出格式:
      ╭─ ⚕ Hermes ─────╮
      <实际回答>          <- 这才是要提取的部分
      ╰─────────────────╯
      Resume this session with: ...
    """
    # 策略 1: 找最后一个 ╭...╮ / 内容 / ╰...╯ 块
    matches = list(re.finditer(
        r"\\u256d[^\\n]+\\u256e\\r?\\n([\\s\\S]*?)\\r?\\n\\u2570\\u2500+\\u256f",
        output,
        re.MULTILINE,
    ))
    if matches:
        response = matches[-1].group(1).strip()
        # 去掉 │ 行包裹
        response = re.sub(r"^\\u2502 ?", "", response, flags=re.MULTILINE)
        response = re.sub(r" ?\\u2502$", "", response, flags=re.MULTILINE)
        response = response.strip()
        if response:
            return response

    # 策略 2: 取 "Resume this session" 之前最后一段非框线文本
    resume_idx = output.find("Resume this session with:")
    chunk = output[:resume_idx].strip() if resume_idx > 0 else output.strip()
    chunk = re.sub(r"[\\u2500-\\u257f\\u2580-\\u259f]+", "", chunk)
    skip = {"Query:", "Initializing", "Session:", "Duration:", "Messages:", "hermes --resume"}
    clean_lines = [l.strip() for l in chunk.splitlines()
                   if l.strip() and not any(l.strip().startswith(x) for x in skip)]
    return "\\n".join(clean_lines).strip() if clean_lines else output.strip()

'''

lines[start:end+1] = [NEW_BLOCK]

with open(path, "w") as f:
    f.writelines(lines)

# Verify we can import it
import ast
with open(path) as f:
    src = f.read()
ast.parse(src)
print("Patched OK, syntax valid")

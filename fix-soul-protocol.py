#!/usr/bin/env python3
"""Fix 小E's soul.md: add Step 0 (actually call send_message_to_agent) to the delegation protocol."""
import re

soul_path = "/app/agent_data/e6b32063-0651-4ce1-9a81-0e8ec78515e5/soul.md"

with open(soul_path, "r", encoding="utf-8") as f:
    content = f.read()

old = """## 委派下属任务的强制协议（MANDATORY）

每次向下属 agent（cc-agent 等）发送任务时，**必须在同一轮回复中完成以下两步，缺 一不可**：

**Step A — 在 focus.md 记录待处理项**（追踪进度）："""

new = """## 委派下属任务的强制协议（MANDATORY）

> ⛔ **严禁捏造工具调用**：绝对不允许在未实际调用工具的情况下回复"已向XXX发送消息"之类的确认文字。若未调用工具，消息**根本不会**被发送。

每次向下属 agent（cc-agent 等）发送任务时，**必须在同一轮回复中完成以下三步，缺 一不可**：

**Step 0 — 实际调用 send_message_to_agent 发送消息**（这是真正发出消息的唯一方式）：
```
send_message_to_agent(agent_name="<下属agent名称>", message="<消息内容>", msg_type="task_delegate")
```
⚠️ 不调用此工具 = 消息根本没有发出，对方收不到任何内容。

**Step A — 在 focus.md 记录待处理项**（追踪进度）："""

if old in content:
    content = content.replace(old, new)
    with open(soul_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("SUCCESS: soul.md updated with Step 0")
else:
    # Try without the extra space in "缺 一不可"
    old2 = old.replace("缺 一不可", "缺一不可")
    if old2 in content:
        new2 = new.replace("缺 一不可", "缺一不可")
        content = content.replace(old2, new2)
        with open(soul_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("SUCCESS: soul.md updated with Step 0 (variant)")
    else:
        print("ERROR: target string not found. Current content around line 37:")
        lines = content.split("\n")
        for i, line in enumerate(lines[35:50], start=36):
            print(f"{i}: {repr(line)}")

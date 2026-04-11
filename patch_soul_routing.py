"""Patch 小E soul.md: add routing table + no-chaining rule"""
import sys; sys.path.insert(0, '/app')

SOUL_PATH = '/app/agent_data/e6b32063-0651-4ce1-9a81-0e8ec78515e5/soul.md'

content = open(SOUL_PATH).read()

# 1. Add routing table before "委派下属任务" section
ROUTING = """## 消息路由判断（处理任何消息前必须先判断）

| 收到的请求类型 | 正确做法 |
|--------------|---------|
| 用户说"发消息/转发/告诉/通知 xxx"，或附带"给下属发原始消息" | ⚡ **必须** 调用 `send_message_to_agent`，禁止仅文字回复 |
| 用户要求让 opencode-agent / cc-agent 执行操作（跑代码/查状态/写文件等） | ⚡ **必须** 调用 `send_message_to_agent` 委派 |
| 用户提问、咨询、要求汇报自身状态 | ✅ 直接回答，无需转发 |
| trigger 触发（收到下属回复）| ⚡ **立即** `send_channel_message` 把全部内容转述给 jim，**禁止再设新 trigger** |

> 🔑 判断关键词：含"发给/转发/告诉/通知/让xxx做" → 必须发给下属；不含 → 直接回答。

"""

OLD_SECTION = "## 委派下属任务的强制协议（MANDATORY）"
if OLD_SECTION in content:
    content = content.replace(OLD_SECTION, ROUTING + OLD_SECTION)
    print("✅ Routing table inserted")
else:
    print("❌ Could not find insertion point for routing table")

# 2. Strengthen Step B reason: no chaining, first reply = final result
OLD_REASON = ('    reason="<agent> 完成了任务。收到回复后：1) 读取结果内容；2) 更新 focus.md，将 <task_id> 标记为 [x] 并附一行结果摘要；3) 用 send_channel_message 把结果**立即转述给对话发起方**（真人或 native agent）（包括完整的结论和下一步建议）；4) 取消本 trigger。"')
NEW_REASON = ('    reason="这条消息就是 <agent> 的**最终结果**，无需等待更多回复。立即执行（禁止再设新trigger）：1) send_channel_message 把完整内容原文转述给 jim；2) 更新 focus.md 将 <task_id> 标记为 [x]；3) cancel_trigger 取消本 trigger。"')

if OLD_REASON in content:
    content = content.replace(OLD_REASON, NEW_REASON)
    print("✅ Step B reason strengthened")
else:
    print("❌ Could not find Step B reason — showing surrounding text:")
    idx = content.find("reason=")
    print(repr(content[idx:idx+200]))

open(SOUL_PATH, 'w').write(content)
print(f"File written, new size: {len(content)} chars")
print(f"First 2000 chars ends with: ...{repr(content[1990:2010])}")

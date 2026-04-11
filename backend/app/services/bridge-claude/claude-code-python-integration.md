# Claude Code CLI — Python 对接指南

## 核心方式：Claude Agent SDK

```bash
pip install claude-agent-sdk
```

---

## 1. 基础对话控制

```python
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, SystemMessage

async def main():
    session_id = None
    
    async for message in query(
        prompt="帮我分析这段代码",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep"],
            cwd="/your/project/path",
            max_turns=20,
        )
    ):
        if isinstance(message, ResultMessage):
            print(f"结果: {message.result}")
            print(f"停止原因: {message.stop_reason}")
        elif isinstance(message, SystemMessage) and message.subtype == "init":
            session_id = message.data.get("session_id")
            print(f"会话 ID: {session_id}")

anyio.run(main)
```

---

## 2. 推送方式获取实时状态更新

`query()` 返回一个异步生成器，每当 Claude 有新动作（工具调用、文本输出、完成等）都会立即 yield，实现真正的推送语义。

```python
from claude_agent_sdk import (
    query, ClaudeAgentOptions,
    AssistantMessage, TextBlock,
    ResultMessage, SystemMessage,
    RateLimitEvent,
)

async def stream_with_updates(prompt: str):
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(allowed_tools=["Read", "Bash", "Edit"])
    ):
        match type(message).__name__:
            case "SystemMessage":
                if message.subtype == "init":
                    print(f"[初始化] 会话: {message.data.get('session_id')}")
            
            case "AssistantMessage":
                # 实时文本输出
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"[回复] {block.text}", end="", flush=True)
                # 用量统计
                if message.usage:
                    print(f"\n[用量] in={message.usage['input_tokens']} out={message.usage['output_tokens']}")
            
            case "ResultMessage":
                print(f"\n[完成] {message.result}")
            
            case "RateLimitEvent":
                print(f"[限流] 状态: {message.rate_limit_info.status}")
```

### 消息类型说明

| 消息类型 | 触发时机 | 关键字段 |
|----------|----------|----------|
| `SystemMessage` (subtype=init) | 会话初始化 | `data.session_id` |
| `AssistantMessage` | Claude 输出文本或调用工具 | `content`, `usage` |
| `ResultMessage` | 任务完成 | `result`, `stop_reason` |
| `RateLimitEvent` | 触发限流 | `rate_limit_info.status`, `resets_at` |
| `TaskStartedMessage` | 子 Agent 任务注册 | — |
| `TaskProgressMessage` | 子 Agent 进度更新 | 累计用量指标 |
| `TaskNotificationMessage` | 子 Agent 任务完成 | `tool_use_id` |

---

## 3. 对话历史与会话管理

```python
from claude_agent_sdk import (
    list_sessions, get_session_messages,
    rename_session, tag_session,
)

# 列出历史会话（同步函数，无需 await）
sessions = list_sessions()
for s in sessions:
    print(f"ID={s.session_id}  目录={s.cwd}")

# 获取指定会话的消息记录
messages = get_session_messages(session_id="your-session-id")

# 重命名/标记会话
rename_session(session_id="...", title="代码重构任务")
tag_session(session_id="...", tag="experiment")

# 清除标签
tag_session(session_id="...", tag=None)
```

---

## 4. 恢复对话（跨 session 保持上下文）

```python
async def resume_conversation(session_id: str, followup: str):
    async for message in query(
        prompt=followup,
        options=ClaudeAgentOptions(resume=session_id)  # 关键：传入 session_id
    ):
        if isinstance(message, ResultMessage):
            print(message.result)
```

---

## 5. 外部控制工具使用（Hooks）

通过 Hook 机制在工具调用前后注入控制逻辑：

```python
from claude_agent_sdk import HookMatcher, ClaudeAgentOptions

async def before_bash(input_data, tool_use_id, context):
    cmd = input_data.get("tool_input", {}).get("command", "")
    print(f"[拦截] 即将执行: {cmd}")
    # 返回 {"decision": "block"} 可以阻止执行
    return {}

async def after_edit(input_data, tool_use_id, context):
    path = input_data.get("tool_input", {}).get("file_path", "")
    print(f"[通知] 文件已修改: {path}")
    return {}

options = ClaudeAgentOptions(
    permission_mode="acceptEdits",
    hooks={
        "PreToolUse":  [HookMatcher(matcher="Bash", hooks=[before_bash])],
        "PostToolUse": [HookMatcher(matcher="Edit|Write", hooks=[after_edit])],
    }
)
```

### 可用 Hook 事件

| 事件 | 说明 |
|------|------|
| `PreToolUse` | 工具调用前，可拦截/阻止 |
| `PostToolUse` | 工具调用成功后 |
| `PostToolUseFailure` | 工具调用失败后 |
| `UserPromptSubmit` | 用户 prompt 提交时 |
| `Stop` | 主 Agent 停止时 |
| `SubagentStop` | 子 Agent 停止时 |
| `PreCompact` | 上下文压缩前 |
| `Notification` | 通知事件 |

---

## 6. 用 `ClaudeSDKClient` 做精细控制（含中断）

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock

async def controlled_session():
    options = ClaudeAgentOptions(allowed_tools=["Read", "Bash"])
    
    async with ClaudeSDKClient(options=options) as client:
        await client.query("扫描项目找出所有 TODO")
        
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text)
                        
                        # 外部条件满足时中断
                        if "危险操作" in block.text:
                            await client.interrupt()
                            break
        
        # MCP 服务器管理
        await client.reconnect_mcp_server("my-server")
        await client.toggle_mcp_server("my-server", enabled=False)
        status = await client.get_mcp_status()
```

---

## 7. 自定义工具（In-Process MCP）

```python
from claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeSDKClient, ClaudeAgentOptions

@tool("get_weather", "获取指定城市的天气", {"location": str})
async def get_weather(args):
    location = args["location"]
    return {"content": [{"type": "text", "text": f"{location} 当前晴天 25°C"}]}

server = create_sdk_mcp_server("my-tools", tools=[get_weather])

async def main():
    options = ClaudeAgentOptions(mcp_servers={"weather": server})
    async with ClaudeSDKClient(options=options) as client:
        await client.query("北京今天天气怎么样？")
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text)
```

---

## 8. 子 Agent（并行任务）

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition, ResultMessage

async def main():
    async for message in query(
        prompt="用 code-reviewer agent 审查这个项目",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "Agent"],
            agents={
                "code-reviewer": AgentDefinition(
                    description="专业代码审查，关注质量与安全。",
                    prompt="分析代码质量并提出改进建议。",
                    tools=["Read", "Glob", "Grep"]
                )
            }
        )
    ):
        if isinstance(message, ResultMessage):
            print(message.result)
```

---

## 9. 权限模式

| 模式 | 说明 |
|------|------|
| `default` | 危险操作前提示用户确认 |
| `plan` | 仅规划，不执行 |
| `acceptEdits` | 自动接受文件编辑 |
| `bypassPermissions` | 跳过所有权限提示（慎用） |

---

## 10. ClaudeAgentOptions 常用参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `cwd` | string | 工作目录 |
| `allowed_tools` | list | 允许使用的工具列表 |
| `disallowed_tools` | list | 禁用的工具列表 |
| `permission_mode` | string | 权限模式 |
| `mcp_servers` | dict | MCP 服务器配置 |
| `hooks` | dict | Hook 回调配置 |
| `system_prompt` | string | 自定义系统提示词 |
| `max_turns` | int | 最大轮次限制 |
| `max_budget_usd` | float | 最大费用限制（美元） |
| `model` | string | 指定模型 ID |
| `resume` | string | 恢复指定会话 ID |
| `agents` | dict | 子 Agent 定义 |

---

## 功能对接汇总

| 功能 | 支持 | 实现方式 |
|------|:----:|----------|
| 发送对话请求 | ✅ | `query()` |
| 实时流式推送 | ✅ | `async for message in query()` |
| 会话恢复 | ✅ | `options.resume=session_id` |
| 历史会话查询 | ✅ | `list_sessions()` / `get_session_messages()` |
| 工具调用拦截 | ✅ | `PreToolUse` Hook |
| 工具调用监听 | ✅ | `PostToolUse` Hook |
| 对话中断 | ✅ | `client.interrupt()` |
| 子 Agent | ✅ | `AgentDefinition` + `Agent` tool |
| MCP 服务器 | ✅ | `mcp_servers` 配置 |
| 自定义工具 | ✅ | `create_sdk_mcp_server()` + `@tool` |
| 限流状态感知 | ✅ | `RateLimitEvent` |
| 权限模式控制 | ✅ | `permission_mode` 参数 |
| 用量/费用追踪 | ✅ | `message.usage` 字段 |
| MCP 服务器管理 | ✅ | `client.reconnect_mcp_server()` / `toggle_mcp_server()` |

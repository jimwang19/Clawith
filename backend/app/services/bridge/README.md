# Clawith Bridge Service

Bridge 服务负责将 Clawith 平台与 OpenCode 本地服务连接，实现双向消息传递和任务协同。

## 架构

```
Clawith Platform (网关) ←→ Bridge 服务 ←→ OpenCode (本地)
```

- 上游：通过 Clawith Gateway API 轮询消息、上报结果
- 下游：通过 OpenCode HTTP+SSE 接口异步通信

## 目录结构

```
backend/app/services/bridge/
├── __init__.py          # Bridge 主程序（包含完整业务逻辑）
├── config.env.example   # 配置文件模板
├── run-forever-example.sh # 持久运行脚本（WSL/容器环境）
└── README.md            # 本文档
```

## 配置项

所有配置通过**环境变量**或**配置文件**设置：

```bash
# Clawith 网关配置
CLAWITH_API_URL=http://localhost:8008       # Clawith 后端地址
CLAWITH_API_KEY=oc-xxx                      # OpenClaw 代理的 API Key

# OpenCode 配置
OPENCODE_HOST=127.0.0.1                     # OpenCode 服务地址
OPENCODE_PORT=4096                          # OpenCode 服务端口
OPENCODE_WORKDIR=/code                      # OpenCode 工作目录

# 轮询和超时配置
POLL_INTERVAL=5                             # 轮询 Clawith 间隔（秒）
OPENCODE_TIMEOUT=300                        # OpenCode 任务超时（秒）

# 高级配置
CLAWITH_SEND_ENABLED=0                      # 是否启用中间状态发送（0=否，1=是）
IDLE_POLL_LOG_EVERY=12                      # 空闲时多久记录一次日志（轮询次数）
PROGRESS_VIA_REPORT=1                       # 是否通过 report 发送进度（1=是，0=否）
INFLIGHT_RECOVER_MAX_AGE=900                # 恢复中断任务的最大年龄（秒）
```

## 运行方式

### 方式一：作为后台服务（独立进程）

适合 WSL/容器环境：

```bash
cd backend/app/services/bridge
cp config.env.example config.env
# 编辑 config.env 填入实际配置

# 直接运行
python3 __init__.py

# 或使用持久运行脚本
bash run-forever-example.sh
```

### 方式二：集成到 Clawith 启动流程

编辑 `backend/app/main.py`，在 `lifespan` 函数中添加：

```python
from app.services.bridge import BridgeService

async def lifespan(app: FastAPI):
    # 现有启动逻辑...
    
    # 启动 Bridge 后台服务
    bridge = BridgeService()
    asyncio.create_task(bridge.run(), name="bridge_service")
    
    yield
```

> **注意**：当前版本 Bridge 是独立进程设计，依赖 OpenCode serve 运行。如果要用 asyncio 方式集成，需要重构为异步客户端。

## 与 OpenClaw Gateway 的关系

Bridge 服务 与现有的 `app/api/gateway.py` 是互补关系：

- **Gateway API**（被动接收）：OpenClaw 代理主动轮询 Clawith 平台
  - `GET /api/gateway/poll` — 代理请求新消息
  - `POST /api/gateway/report` — 代理上报结果
  - `POST /api/gateway/heartbeat` — 代理心跳保活

- **Bridge 服务**（主动轮询）：作为 OpenClaw 代理运行在 Clawith 平台
  - 主动轮询 `/api/gateway/poll` 获取消息
  - 调用 `/api/gateway/report` 上报 OpenCode 的结果
  - 自动发送心跳保持连接

**本质上，Bridge 是一个特殊的 OpenClaw 代理**，它将消息转发给 OpenCode 处理。

## 与 OpenCode 的对接

Bridge 通过以下 OpenCode HTTP 接口通信：

1. **Health Check**
   ```
   GET /global/health
   ```

2. **创建会话**
   ```
   POST /session
   Body: {"title": "clawith-{conversation_id}"}
   ```

3. **异步发送消息**
   ```
   POST /session/{session_id}/prompt_async
   Body: {"parts": [{"type": "text", "text": "消息内容"}]}
   Response: 204 No Content (立即返回，不阻塞)
   ```

4. **SSE 监听**
   ```
   GET /event
   返回 Server-Sent Events 流，监听:
   - session.status (idle/busy/error)
   - message.part.updated (工具调用、文本生成)
   - permission.requested (权限请求)
   ```

5. **权限响应**
   ```
   POST /session/{session_id}/permissions/{permission_id}
   Body: {"response": "allow|deny", "remember": false}
   ```

6. **获取历史消息**
   ```
   GET /session/{session_id}/message
   ```

## 工作流程

1. **启动阶段**
   - 等待 OpenCode serve 上线
   - 恢复上次中断的 in-flight 任务
   - 重用已有 OpenCode session 缓存
   - 启动 SSE 监听线程和超时监控线程

2. **消息处理循环**
   ```
   轮询 Clawith → 收到消息 → 创建/复用 OpenCode Session 
   → prompt_async 发送 → SSE 监听事件 → 推送状态 → 最终 result 报告
   ```

3. **状态推送**
   - 工具调用：`正在调用工具：xxx...`
   - 处理中：`OpenCode 正在处理...`
   - 权限请求：`⚠️ OpenCode 请求权限确认`
   - 完成：通过 `/api/gateway/report` 发送最终结果

## 关键特性

### 1. Session 复用
同一 `conversation_id` 复用同一个 OpenCode session，保持对话历史连贯。Bridge 崩溃重启后会自动从 OpenCode 恢复 session 映射。

### 2. In-flight 持久化
任务状态实时保存到 `logs/inflight.json`，Bridge 重启时可恢复中断状态并通知用户。

### 3. 超时监控
- 超过 `OPENCODE_TIMEOUT` 秒的任务自动标记为超时
- 每 60 秒轮询活跃 session 状态（防止 SSE 事件丢失）

### 4. 权限确认
当 OpenCode 请求高风险操作权限时，可转发给 Clawith 用户确认（需 `CLAWITH_SEND_ENABLED=1`）。

### 5. 并发控制
同一会话只允许一个任务进行中，避免消息竞争和状态污染。

## 日志

日志文件位于 `logs/` 目录：
- `bridge.log` — Bridge 主程序日志
- `opencode.log` — OpenCode serve 日志
- `run-forever.log` — 守护进程日志
- `inflight.json` — 进行中的任务状态
- `*.pid` — 进程 ID 文件

## 故障排查

**Bridge 无法连接 Clawith**
- 检查 `CLAWITH_API_URL` 和 `CLAWITH_API_KEY` 配置
- 确认 OpenClaw 代理类型正确（`agent_type='openclaw'`）
- 检查网络连通性

**OpenCode 无响应**
- 检查 `OPENCODE_HOST` 和 `OPENCODE_PORT`
- 确认 `opencode serve` 正在运行
- 查看 `logs/opencode.log`

**SSE 断连**
- Bridge 会自动重连（3 秒重试）
- 检查 OpenCode 服务是否重启
- 检查防火墙/代理设置

**任务中断**
- 查看 `logs/inflight.json` 了解未完成任务
- Bridge 重启时会自动汇报中断信息

## 进阶配置

### 使用 Docker 运行

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY backend/app/services/bridge /app/bridge
COPY backend/requirements.txt /app/
RUN pip install -r /app/requirements.txt
ENV CLAWITH_API_URL=http://backend:8008
ENV OPENCODE_HOST=host.docker.internal
CMD ["python3", "/app/bridge/__init__.py"]
```

### 配置系统服务（systemd）

```ini
# /etc/systemd/user/clawith-bridge.service
[Unit]
Description=Clawith Bridge Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/clawith/backend/app/services/bridge
EnvironmentFile=/path/to/config.env
ExecStart=/usr/bin/python3 /path/to/bridge/__init__.py
Restart=always

[Install]
WantedBy=default.target
```

运行：
```bash
systemctl --user enable clawith-bridge
systemctl --user start clawith-bridge
```

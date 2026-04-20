# Morty 消息转发问题修复方案

## 问题根因

### 1. Morty 没有发送确认机制
- Morty 显示"✅ 已执行"但实际没有检查 Hermes Bridge 是否正常运行
- 9:57 发送消息时，Hermes OpenClaw Bridge 正在崩溃重启（日志显示 10:03 才稳定）
- **虚假成功反馈**：用户看到"已发送"，但消息实际丢失

### 2. 没有重试机制
- Morty 的心智中只有"发送"动作，没有"检查发送结果"和"重试"逻辑
- Hermes Bridge 崩溃期间的消息全部丢失

### 3. Hermes Bridge 不稳定
- 日志显示 6 分钟内重启 6+ 次（09:41-10:09）
- 每次重启导致消息轮询中断

---

## 修复方案

### 修复 1: Morty 心智添加发送确认

在 Morty 的 persona 中添加：

```markdown
## 委托任务给 Hermes Agent 的标准流程

### 步骤 1: 发送前检查
1. 先确认 Hermes Bridge 状态（可选）
2. 准备委托消息内容

### 步骤 2: 发送并等待确认
1. 调用 Clawith API 发送消息
2. **等待 API 返回成功状态（HTTP 200）**
3. 如果返回失败，进入重试流程

### 步骤 3: 反馈结果给用户
**成功**：
"✅ 已成功委托给 Hermes Agent，正在等待处理..."

**失败（重试后）**：
"⚠️ Hermes Bridge 似乎不在运行，已重试 3 次仍未成功。请稍后再试或检查 Hermes 服务状态。"

**失败（立即）**：
"❌ 无法连接到 Hermes Bridge（API 返回错误）。请稍后重试。"
```

---

### 修复 2: 添加重试逻辑

修改 Morty 的转发代码（伪代码）：

```python
async def forward_to_hermes_with_retry(task_description, max_retries=3):
    """
    向 Hermes 转发消息，带重试机制
    """
    for attempt in range(1, max_retries + 1):
        try:
            # 发送消息到 Clawith Gateway
            status, response = await clawith_send_message(
                agent_name="Hermes",
                content=task_description
            )
            
            if status == 200:
                # 发送成功
                return {
                    "success": True,
                    "message": "✅ 已成功委托给 Hermes Agent"
                }
            
            # 发送失败，记录日志
            log.warning(f"Forward attempt {attempt}/{max_retries} failed: HTTP {status}")
            
        except Exception as e:
            log.error(f"Forward attempt {attempt}/{max_retries} exception: {e}")
        
        # 指数退避等待
        if attempt < max_retries:
            wait_time = 2 ** attempt  # 2, 4, 8 秒
            await asyncio.sleep(wait_time)
    
    # 所有重试都失败
    return {
        "success": False,
        "message": f"⚠️ 已重试 {max_retries} 次，Hermes Bridge 仍无响应。请稍后检查服务状态。"
    }
```

---

### 修复 3: Hermes Bridge 健康检查

在 Morty 发送前增加健康检查：

```python
async def check_hermes_bridge_health():
    """
    检查 Hermes Bridge 是否健康
    """
    try:
        status, body = await http_get(
            "http://127.0.0.1:8888/health",
            timeout=5
        )
        
        if status == 200 and body.get("status") == "ok":
            return True
        return False
        
    except Exception:
        return False

# 在转发前使用
if not await check_hermes_bridge_health():
    return "⚠️ Hermes Bridge 当前不在线，无法转发消息。正在尝试重启..."
```

---

### 修复 4: Gateway 消息队列持久化

建议在 Clawith 后端添加：

```python
# 消息先写入 SQLite，Bridge 重启后继续消费
# 避免消息丢失

# app/api/gateway.py
def poll_messages(api_key):
    # 从数据库获取未消费消息
    messages = db.query(
        Message
    ).filter(
        Message.agent_name == "Hermes",
        Message.consumed == False,
        Message.api_key == api_key
    ).all()
    
    # 标记为已消费
    for msg in messages:
        msg.consumed = True
    
    db.commit()
    return messages
```

---

### 修复 5: 增强 Morty 的用户反馈

修改 Morty 的响应模板：

```markdown
## Morty 响应用户的新模板

### 场景 A: 发送成功
```
✅ 已执行

刚刚向 Hermes Agent 发送了委托消息：
"你好 Hermes，Jim 委托我向你请求帮助：[任务内容]"

📡 Hermes 已确认接收，正在处理中...
```

### 场景 B: 发送失败（可重试）
```
⚠️ 发送失败

尝试向 Hermes Agent 发送委托消息时遇到错误：
- HTTP 状态码：503 Service Unavailable
- 已重试 3 次，仍未成功

建议操作：
1. 检查 Hermes Bridge 服务状态
2. 稍后重试
3. 或我帮你尝试其他方式执行任务？
```

### 场景 C: 发送失败（无重试）
```
❌ 无法连接到 Hermes

Hermes Bridge 当前未响应，可能正在维护或崩溃。

技术细节：
- 错误类型：Connection refused
- 目标地址：http://127.0.0.1:8888
- 时间：2026-04-16 09:57

需要我帮你：
1. 尝试重启 Hermes Bridge
2. 用其他方式执行任务
3. 稍后重试
```
```

---

## 实施优先级

### P0（立即修复）
1. **Morty 添加发送确认** - 防止虚假成功反馈
2. **Morty 添加重试机制** - 3 次指数退避重试

### P1（本周内）
3. **Hermes Bridge 健康检查** - 发送前验证服务可用
4. **Gateway 消息持久化** - 防止消息丢失

### P2（下次迭代）
5. **Morty 响应模板优化** - 改进用户反馈
6. **Hermes Bridge 稳定性改进** - 减少崩溃频率

---

## 立即可执行的命令

```bash
# 1. 检查当前 Hermes Bridge 状态
wsl -d ubu24.04-opcdev bash -c "curl -s http://127.0.0.1:8888/health"

# 2. 检查 Gateway 队列中是否有 Morty 的未消费消息
wsl -d ubu24.04-opcdev bash -c "curl -s -X GET 'http://127.0.0.1:8000/api/gateway/poll' -H 'X-Api-Key: oc-K1bfv3rRSdJJGPaL1IfCZmAxJMGMbdAbawB6Ngw8SeU'"

# 3. 查看 Hermes Bridge 崩溃原因
wsl -d ubu24.04-opcdev bash -c "grep 'error\\|Exception\\|failed' /home/ubuntu/clawith-bridge-hermes/logs/run-forever.log | tail -20"

# 4. 重启 Hermes Bridge 服务
wsl -d ubu24.04-opcdev bash -c "sudo systemctl restart hermes-bridge.service"
```

---

## 验证标准

修复完成后，Morty 的行为应该是：

1. ✅ 发送消息后等待 API 确认
2. ✅ 发送失败时明确告知用户
3. ✅ 自动重试 3 次（指数退避）
4. ✅ 重试失败后提供替代方案
5. ✅ 不再出现"已发送但实际丢失"的情况

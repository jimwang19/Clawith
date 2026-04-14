# bridge-claude 设计决策备忘

## 新增功能（2026-04-14）

### 并发控制
- 全局并发上限 **2**（`MAX_CONCURRENT_TASKS`，环境变量可覆盖）
- 超限直接拒绝，返回 429 消息给 Clawith，不排队
- `try_acquire_slot()` / `release_slot()` 管理槽位

### Status HTTP Server（端口 8765）
独立端口，不影响与 Clawith gateway 的交互协议。

| 接口 | 说明 |
|------|------|
| `GET /status` | 所有活跃任务列表，含 elapsed_s、tool_calls、last_progress |
| `GET /status/{msg_id}` | 单任务详情，404 表示不存在或已完成 |
| `POST /session/{conv_id}/decide` | 注入权限决策 `{"decision": "allow"\|"deny"}`，替代原来靠 Clawith 消息回复触发 |

端口可通过 `BRIDGE_STATUS_PORT` 环境变量覆盖。

### 权限决策流程变化
原来：Clawith 用户回复"允许"/"拒绝" → bridge poll 到消息 → 触发决策

现在新增：主控 AI / Clawith 平台层直接调 `POST /session/{conv_id}/decide` 注入决策，更快、不依赖消息轮询间隔。

---

## 测试策略

测试脚本: `test_e2e_bridge.py`

### 注入方式

| 方式 | 需要 | 限制 |
|------|------|------|
| DB 直注 (psycopg2) | `CLAWITH_DB_URL` + `pip install psycopg2-binary` | 无，推荐 |
| Gateway self-send | `BRIDGE_AGENT_NAME` | 同一 sender→receiver 的 conv_id 固定，并发测试受限 |

推荐在 `.env` 配置 `CLAWITH_DB_URL`，可解锁并发测试 (T05)。

### 测试场景

| ID | 场景 | 前提 |
|----|------|------|
| T01 | GET /status 可达 | bridge 运行中 |
| T02 | Clawith poll/heartbeat 连通 | CLAWITH_API_KEY 已配置 |
| T03 | E2E: 注入 → bridge 接收 → 完成 | CLAWITH_DB_URL 或 BRIDGE_AGENT_NAME |
| T04 | 权限决策: allow/deny 注入 | PERM_MODE=default 且有注入方式 |
| T05 | 并发上限: 3任务仅2接受 | CLAWITH_DB_URL (独立 conv_id) |
| T06 | 中断恢复: inflight → 重启 → 日志 | 传 `--test-recovery` 标志 |

### 运行示例

```bash
# 完整测试 T01-T05
python3 test_e2e_bridge.py

# 含重启恢复测试
python3 test_e2e_bridge.py --test-recovery

# 只跑指定场景
python3 test_e2e_bridge.py --only T03
```

### Status 输出变化

`GET /status` 和 `GET /status/{msg_id}` 的 task 对象新增 `request_preview` 字段 (首120字符)，供自动化测试按内容标记识别任务。

---


**决策：bridge 不做，由 Clawith 上层处理。**

场景：用户问"我刚才发的'xxxxxx'处理了吗？"

处理方式：
1. 数字人/主控 AI 提取关键词
2. 调 `GET /status` 拿活跃任务列表
3. 在 `request_preview` 字段里匹配
4. 回复用户

bridge 保持纯粹的机器接口，不加 `?q=` 模糊匹配。

---

## 与 Clawith gateway 的交互不变

- bridge 仍然 poll `GET /api/gateway/poll` 取消息
- 结果仍然通过 `POST /api/gateway/report` 回报
- Status server 是额外能力，对现有流程无侵入

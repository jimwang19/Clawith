# Bridge-Claude 今日改进总结与 run-forever.sh 使用说明

更新时间：2026-04-15

## 1. 今日完成的改进

### 1.1 Claude 子进程生命周期与回收
- 在 `__main__.py` 中增加任务级别的子进程追踪。
- 增加 Claude 运行过程中子进程的优雅回收流程。
- 增加退出路径上的清理处理，减少异常中断后的残留进程。
- 优化任务线程生命周期，降低僵尸进程/孤儿进程风险。

### 1.2 Session 机制调整
- 默认行为：同一会话有历史 session 时继续复用。
- 增加显式“新会话”命令，下一条消息强制开新 session：
  - `/new-session`
  - `/新会话`
  - `/reset`

### 1.3 WSL 部署与启动改进
- 部署目标路径统一为：
  - `/home/jim/clawith-bridge-claude`
- 修复 Windows 到 WSL 的路径转换问题。
- `run-forever.sh` 增加 `status` 子命令，方便本地快速检查。

### 1.4 SSH 隧穿自愈整合
- `run-forever.sh` 增加隧穿健康检查，异常时自动重拉。
- 隧穿启动与 Bridge 启动纳入同一个 watchdog 流程。
- 健康检查改为“连通性判定”，不再强依赖 HTTP 2xx。

### 1.5 CC 环境切换改造（去掉写死路径）
- 运行逻辑中不再写死 `CC_ENV_SCRIPT`。
- 增加环境脚本解析优先级：
  1. 进程环境变量 `CC_ENV_SCRIPT`
  2. 选择文件 `/home/jim/clawith-bridge-claude/.cc_env_script`
  3. 别名文件 `/mnt/c/Users/*/cc_env_current.sh`
  4. 当且仅当全局仅有一个 `/mnt/c/Users/*/cc_env_*.sh` 时自动选中
- `run-forever.sh` 增加 env 管理子命令（`list/current/use`）。

## 2. run-forever.sh 使用说明

先进入 WSL 目录：

```bash
cd /home/jim/clawith-bridge-claude
```

### 2.1 启动方式

```bash
bash run-forever.sh
nohup bash run-forever.sh &
```

### 2.2 状态检查

```bash
bash run-forever.sh status
```

会输出：
- watchdog 进程信息
- bridge 主进程信息
- bridge HTTP 状态接口输出

### 2.3 环境脚本管理

列出可用环境脚本：

```bash
bash run-forever.sh env list
```

查看当前解析结果：

```bash
bash run-forever.sh env current
```

选择环境脚本（写入选择文件，持久生效）：

```bash
bash run-forever.sh env use /mnt/c/Users/jimwa/cc_env_jeniya.sh
bash run-forever.sh env use /mnt/c/Users/jimwa/cc_env_nuoda.sh
bash run-forever.sh env use /mnt/c/Users/jimwa/cc_env_zhihui.sh
```

说明：
- 切换 env 后，一般不需要重启 `run-forever.sh`，后续新任务会使用新环境。
- 正在执行中的任务仍使用其启动时环境。
- 如果改的是 `.env` 里的配置项，建议重启服务。

### 2.4 Claude 会话控制

默认：有历史 session 时继续复用。

发送以下任意命令，可让下一条消息进入全新 session：

```text
/new-session
/新会话
/reset
```

### 2.5 日志与健康检查

```bash
tail -f logs/run-forever.log
tail -f logs/bridge.log
curl http://127.0.0.1:8765/status
curl -m 5 -o /dev/null -sS http://127.0.0.1:8000/api/gateway/poll && echo tunnel-ok || echo tunnel-fail
```

## 3. 日常操作建议

1. 确认当前环境脚本

```bash
bash run-forever.sh env current
```

2. 确认运行状态

```bash
bash run-forever.sh status
```

3. 如需切换环境后复核

```bash
bash run-forever.sh env use /mnt/c/Users/jimwa/cc_env_xxx.sh
bash run-forever.sh env current
```

4. 若大改配置后行为异常，可重启服务

```bash
sudo systemctl restart clawith-bridge-claude.service
```

## 4. 今日核心变更文件

- `backend/app/services/bridge-claude/__main__.py`
- `backend/app/services/bridge-claude/run-forever.sh`
- `backend/app/services/bridge-claude/deploy-to-wsl.sh`

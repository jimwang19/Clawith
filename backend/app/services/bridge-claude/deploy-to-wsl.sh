#!/bin/bash
#
# deploy-to-wsl.sh - Deploy bridge-claude to WSL Ubuntu environment
# Usage: bash deploy-to-wsl.sh
#

set -e

BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"

# WSL 目标路径（用户已部署目录）
WSL_TARGET_DIR="/home/jim/clawith-bridge-claude"

echo "=========================================="
echo "Bridge-Claude WSL Deployment"
echo "=========================================="
echo "Source:      $BRIDGE_DIR"
echo "Target (WSL): $WSL_TARGET_DIR"
echo "CC Env:      resolved at runtime by run-forever.sh"
echo ""

# 创建 WSL 目标目录
wsl bash -c "mkdir -p '$WSL_TARGET_DIR' && mkdir -p '$WSL_TARGET_DIR/logs'"

# 复制关键文件
echo "📋 Copying files..."
files_to_copy=(
    "__main__.py"
    "run-forever.sh"
    ".env"
)

for file in "${files_to_copy[@]}"; do
    src="$BRIDGE_DIR/$file"
    if [ -f "$src" ]; then
        # 转换路径供 WSL 访问（兼容 Git Bash / MSYS 路径）
        win_src="$(wsl wslpath -a "$src" 2>/dev/null || true)"
        if [ -z "$win_src" ]; then
            win_src="/mnt/$(printf '%s\n' "$src" | sed 's#^/##')"
        fi
        echo "  ✓ $file"
        wsl bash -c "cp '$win_src' '$WSL_TARGET_DIR/'"
    else
        echo "  ✗ $file (not found)"
    fi
done

# 在 WSL 中设置权限
echo "🔐 Setting permissions..."
wsl bash -c "chmod +x '$WSL_TARGET_DIR/__main__.py' '$WSL_TARGET_DIR/run-forever.sh'"

# 验证部署
echo ""
echo "✅ Deployment complete!"
echo ""
echo "=========================================="
echo "Next Steps:"
echo "=========================================="
echo ""
echo "1. Enter WSL:"
echo "   wsl bash"
echo ""
echo "2. Navigate to bridge-claude:"
echo "   cd $WSL_TARGET_DIR"
echo ""
echo "3. Review environment (.env):"
echo "   cat .env"
echo ""
echo "4. Test CC environment loading:"
echo "   bash run-forever.sh env list"
echo "   bash run-forever.sh env use /mnt/c/Users/jimwa/cc_env_xxx.sh"
echo "   bash run-forever.sh env current"
echo ""
echo "5. Start bridge-claude:"
echo "   bash run-forever.sh          # 前台运行（调试）"
echo "   nohup bash run-forever.sh &  # 后台持久运行"
echo ""
echo "6. Check status:"
echo "   ps aux | grep __main__.py"
echo "   tail -f logs/run-forever.log"
echo "   tail -f logs/bridge.log"
echo ""
echo "7. Monitor via HTTP (from WSL):"
echo "   curl http://127.0.0.1:8765/status | jq"
echo ""

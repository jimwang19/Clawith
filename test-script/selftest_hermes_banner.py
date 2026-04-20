"""
Hermes bridge banner 过滤自测

测试 _strip_banner 在各种真实 hermes 输出格式下的过滤效果，
确保 Available Tools / Skills banner 不会混入 report 结果。

Run:
  python3 selftest_hermes_banner.py
  或在 WSL 里:
  python3 /mnt/d/jim/aiwork/github/Clawith/selftest_hermes_banner.py
"""

import re
import sys

# ── 从 bridge 文件直接加载 _strip_banner ─────────────────────────────────────
BRIDGE_PATH = "/home/ubuntu/clawith-bridge-hermes/hermes-openclaw-bridge.py"

try:
    src = open(BRIDGE_PATH).read()
    # 提取 _strip_banner 函数
    fn_src = re.search(r"(def _strip_banner\(.*?)(?=\ndef |\Z)", src, re.DOTALL)
    if not fn_src:
        raise ValueError("_strip_banner not found in bridge file")
    ns = {}
    exec("import re\n" + fn_src.group(1), ns)
    _strip_banner = ns["_strip_banner"]
    print(f"✅ Loaded _strip_banner from {BRIDGE_PATH}\n")
except Exception as e:
    print(f"❌ Failed to load from bridge: {e}")
    sys.exit(1)


# ── 测试用例 ─────────────────────────────────────────────────────────────────

CASES = [
    {
        "desc": "C1 纯 banner，无业务内容（心跳/启动消息）",
        "input": """\
   Available Tools
   browser: browser_back, browser_click, ...
   clarify: clarify
   code_execution: execute_code
   delegation: delegate_task
   file: patch, read_file, search_files,
   write_file
   homeassistant: ha_call_service, ...
   (and 11 more toolsets...)

   Available Skills
   autonomous-ai-agents: claude-code, codex,
   hermes-agent, opencode
""",
        "expect_contains": [],
        "expect_not_contains": ["Available Tools", "Available Skills", "browser:", "clarify:", "(and "],
        "expect_empty": True,
    },
    {
        "desc": "C2 banner + 真实业务回复",
        "input": """\
   Available Tools
   browser: browser_back, browser_click, ...
   clarify: clarify
   (and 11 more toolsets...)

   Available Skills
   autonomous-ai-agents: claude-code, hermes-agent

OPC政策最新动态（2026年4月）：
1. 北京：注册资本最低1元，享受3年税收优惠
2. 上海：一人公司可申请创业补贴最高10万元
3. 深圳：简化注册流程，1天内完成工商登记
""",
        "expect_contains": ["OPC政策", "北京", "上海", "深圳"],
        "expect_not_contains": ["Available Tools", "Available Skills", "browser:", "(and "],
        "expect_empty": False,
    },
    {
        "desc": "C3 旧格式框线输出（策略1，应原样提取）",
        "input": """\
╭─ ⚕ Hermes ──────────────────────────────────────────────╮
│ OPC一人公司政策调研完成。主要发现：各地政策差异显著。    │
╰──────────────────────────────────────────────────────────╯
Resume this session with: hermes --resume abc123
""",
        "expect_contains": ["OPC一人公司政策"],
        "expect_not_contains": ["Resume this session", "hermes --resume"],
        "expect_empty": False,
    },
    {
        "desc": "C4 无 banner 纯业务文本",
        "input": "这是 Hermes 的直接回复，没有任何 banner。",
        "expect_contains": ["这是 Hermes 的直接回复"],
        "expect_not_contains": [],
        "expect_empty": False,
    },
    {
        "desc": "C5 真实复现：钉钉触发器收到的心跳消息格式",
        "input": """\
   Available Tools
 \u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u28c0\u2843\u2800\u2843\u2843\u2800\u28c0\u2843\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800   browser: browser_back, browser_click, ...
   clarify: clarify
   code_execution: execute_code
   delegation: delegate_task
   file: patch, read_file, search_files,
   write_file
   homeassistant: ha_call_service,
   ha_get_state, ...
   image_gen: image_generate
   (and 11 more toolsets...)

   Available Skills
   autonomous-ai-agents: claude-code, codex,
   hermes-agent, opencode
""",
        "expect_contains": [],
        "expect_not_contains": ["Available Tools", "browser:", "clarify:"],
        "expect_empty": True,
    },
]


# ── 执行测试 ─────────────────────────────────────────────────────────────────

passed = []
failed = []

for i, case in enumerate(CASES, 1):
    label = f"[{i:02d}/{len(CASES)}]"
    print(f"{label} {case['desc']}")

    result = _strip_banner(case["input"])

    ok = True
    detail = []

    for kw in case.get("expect_contains", []):
        if kw not in result:
            ok = False
            detail.append(f"missing '{kw}'")

    for kw in case.get("expect_not_contains", []):
        if kw in result:
            ok = False
            detail.append(f"should not contain '{kw}'")

    if case.get("expect_empty") and result.strip():
        ok = False
        detail.append(f"expected empty but got: {repr(result[:80])}")

    if ok:
        print(f"  ✅ pass | result: {repr(result[:80])}")
        passed.append(i)
    else:
        print(f"  ❌ FAIL | {'; '.join(detail)}")
        print(f"     result: {repr(result[:120])}")
        failed.append(i)

print(f"\n{'='*60}")
print(f"✅ PASSED ({len(passed)}): {passed}")
print(f"❌ FAILED ({len(failed)}): {failed}")
print(f"通过率: {len(passed)}/{len(CASES)}")

if failed:
    print("\n需要修复 ↑")
    sys.exit(1)
else:
    print("\n🎉 全部通过！")

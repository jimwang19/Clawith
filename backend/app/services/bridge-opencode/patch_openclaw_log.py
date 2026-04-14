#!/usr/bin/env python3
"""Patch @laceletho/plugin-openclaw to respect LOG_LEVEL for INFO messages."""
import sys

fp = "/home/jim/.cache/opencode/packages/@laceletho/plugin-openclaw@latest/node_modules/@laceletho/plugin-openclaw/dist/index.js"

with open(fp) as f:
    src = f.read()

old = 'info: (message, meta) => log("INFO", message, meta),'
new = ('info: (message, meta) => {'
       ' const _lvl = (process.env.LOG_LEVEL || "info").toLowerCase();'
       ' if (_lvl === "warn" || _lvl === "error") return;'
       ' log("INFO", message, meta); },')

if old not in src:
    print("ERROR: pattern not found, already patched or file changed")
    sys.exit(1)

src = src.replace(old, new, 1)
with open(fp, "w") as f:
    f.write(src)

print("patched OK")

#!/usr/bin/env python3
path = "/app/agent_data/e6b32063-0651-4ce1-9a81-0e8ec78515e5/soul.md"
content = open(path).read()
print("Total chars:", len(content))
pos = content.find("Step 0")
print("Step 0 position:", pos)
print("2000-char cutoff shows:", repr(content[1980:2020]))
print()
# What gets cut at 2000 chars
print("Last 100 chars of 2000-window:")
print(content[1900:2000])

#!/bin/bash
# Check recent gateway messages for opencode-agent
# First find opencode-agent's ID
API_KEY="oc-GIJ9VHco902D1BZvzD16bCbwoxXtDzQCFP32kmYzvhs"
BASE_URL="http://100.123.217.100:8000"

echo "=== /api/messages/inbox ==="
curl -s -H "X-API-Key: $API_KEY" "$BASE_URL/api/messages/inbox" | python3 -m json.tool 2>/dev/null || echo "(raw:)" && curl -s -H "X-API-Key: $API_KEY" "$BASE_URL/api/messages/inbox"

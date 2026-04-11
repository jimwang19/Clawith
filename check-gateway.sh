#!/bin/bash
API_KEY="oc-GIJ9VHco902D1BZvzD16bCbwoxXtDzQCFP32kmYzvhs"
BASE_URL="http://100.123.217.100:8000"

# poll latest to get agent context
echo "=== gateway/poll (full) ==="
curl -s -H "X-Api-Key: $API_KEY" "$BASE_URL/api/gateway/poll"
echo ""

# Check gateway messages - need agent_id, try to get it from setup-guide or generate-key
# Let's do a heartbeat and check for agent info in response
echo "=== gateway/heartbeat ==="
curl -s -X POST -H "X-Api-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"status":"idle","extra":{}}' "$BASE_URL/api/gateway/heartbeat"
echo ""

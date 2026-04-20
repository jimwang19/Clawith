#!/bin/bash
# Send a real test query to cc-agent

curl -s -X POST http://127.0.0.1:8000/api/gateway/push \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "cc-agent",
    "content": "简单测试：1+1等于多少？"
  }' | tee /tmp/test_query_response.json

echo
echo "Response saved to /tmp/test_query_response.json"
sleep 3
echo "Bridge status after query:"
curl -s http://127.0.0.1:8765/status | python3 -m json.tool 2>/dev/null || curl -s http://127.0.0.1:8765/status

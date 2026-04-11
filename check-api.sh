#!/bin/bash
curl -s 'http://100.123.217.100:8000/openapi.json' > /tmp/api.json
python3 << 'PYEOF'
import json
d = json.load(open("/tmp/api.json"))
for k in d["paths"].keys():
    if any(x in k.lower() for x in ["gateway", "message", "conv", "chat"]):
        print(k)
PYEOF

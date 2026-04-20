#!/bin/bash
docker inspect a9383ed831c0 2>/dev/null > /tmp/container_inspect.json
python3 - << 'EOF'
import json
with open('/tmp/container_inspect.json') as f:
    c = json.load(f)[0]
print("Name:", c.get('Name',''))
print("Entrypoint:", c['Config'].get('Entrypoint',''))
print("Cmd:", c['Config'].get('Cmd',''))
print("RestartPolicy:", c['HostConfig'].get('RestartPolicy',''))
EOF

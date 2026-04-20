import json, subprocess, sys

result = subprocess.run(
    ["docker", "inspect", "a9383ed831c0"],
    capture_output=True, text=True
)
if result.returncode != 0:
    print("Error:", result.stderr)
    sys.exit(1)

c = json.loads(result.stdout)[0]
print("Name:", c.get("Name", ""))
print("Entrypoint:", c["Config"].get("Entrypoint", ""))
print("Cmd:", c["Config"].get("Cmd", ""))
print("RestartPolicy:", c["HostConfig"].get("RestartPolicy", ""))
print("Status:", c["State"].get("Status", ""))

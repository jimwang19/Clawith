import subprocess, json

out = subprocess.check_output(['docker', 'inspect', 'hermes-agent']).decode()
d = json.loads(out)[0]
nets = d['NetworkSettings']['Networks']
for k, v in nets.items():
    print(f'Network: {k}, IP: {v["IPAddress"]}')
print(f'NetworkMode: {d["HostConfig"]["NetworkMode"]}')
print(f'RestartPolicy: {d["HostConfig"]["RestartPolicy"]["Name"]}')
print(f'Status: {d["State"]["Status"]}')
print(f'Image: {d["Config"]["Image"]}')

# Check env vars for Clawith integration
env = {e.split("=")[0]: e.split("=",1)[1] for e in d["Config"]["Env"] if "=" in e}
clawith_keys = [k for k in env if "CLAWITH" in k or "GATEWAY" in k or "API_URL" in k or "OPENCODE" in k or "BACKEND" in k]
print(f'\nClawith-related env vars:')
for k in clawith_keys:
    print(f'  {k}={env[k]}')
if not clawith_keys:
    print('  (none found)')

# All env keys
print(f'\nAll env keys: {list(env.keys())}')

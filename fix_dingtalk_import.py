content = open('/app/app/api/dingtalk.py').read()
old = "        from app.models.agent import DEFAULT_CONTEXT_WINDOW_SIZE\n        ctx_size = (agent_obj.context_window_size or DEFAULT_CONTEXT_WINDOW_SIZE) if agent_obj else DEFAULT_CONTEXT_WINDOW_SIZE"
new = "        ctx_size = agent_obj.context_window_size or 100"
content = content.replace(old, new)
open('/app/app/api/dingtalk.py', 'w').write(content)
print('done' if 'DEFAULT_CONTEXT_WINDOW_SIZE' not in content else 'FAILED - string not found or not replaced')

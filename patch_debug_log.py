"""Add a temporary debug log to call_llm in websocket.py"""
import sys; sys.path.insert(0, '/app')

path = '/app/app/api/websocket.py'
content = open(path).read()

old = '    # Load tools dynamically from DB\n    tools_for_llm = await get_agent_tools_for_llm(agent_id) if agent_id else AGENT_TOOLS'
new = ('    # Load tools dynamically from DB\n'
       '    tools_for_llm = await get_agent_tools_for_llm(agent_id) if agent_id else AGENT_TOOLS\n'
       '    logger.info(f"[DEBUG-TOOLS] agent_id={agent_id} tools_count={len(tools_for_llm)} '
       'tool_names={[t.get(chr(102)+chr(117)+chr(110)+chr(99)+chr(116)+chr(105)+chr(111)+chr(110),{}).get(chr(110)+chr(97)+chr(109)+chr(101)) for t in tools_for_llm[:5]]}")')

if old in content:
    content = content.replace(old, new)
    open(path, 'w').write(content)
    print('Inserted debug log OK')
else:
    print('String NOT found - check exact text')
    # Show surrounding area
    idx = content.find('tools_for_llm = await get_agent_tools_for_llm')
    print(repr(content[idx-60:idx+120]))

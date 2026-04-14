import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Get actual message content from parts table for main session
session_id = 'ses_29053f92fffeXHSo1bTDcnMq57'

print(f'DETAILED MESSAGE CONTENT FOR MAIN SESSION: {session_id}')
print('=' * 80)
print()

# Get all parts for this session
cursor.execute('''
    SELECT p.id, p.message_id, p.time_created, p.data
    FROM part p
    WHERE p.session_id = ?
    ORDER BY p.time_created
''', (session_id,))

parts = cursor.fetchall()
print(f'Total parts: {len(parts)}')
print()

# Extract and categorize content
text_content = []
tool_calls = []
reasoning_content = []

for part_id, message_id, time_created, data in parts:
    try:
        part_data = json.loads(data)
        part_type = part_data.get('type', 'unknown')
        
        if part_type == 'text':
            text = part_data.get('text', '')
            text_content.append(text)
        elif part_type == 'tool':
            tool_name = part_data.get('tool', 'unknown')
            tool_state = part_data.get('state', {})
            tool_calls.append({'tool': tool_name, 'state': tool_state})
        elif part_type == 'reasoning':
            reasoning = part_data.get('text', '')
            reasoning_content.append(reasoning)
    except:
        pass

print('TEXT CONTENT SAMPLES:')
print('-' * 80)
for i, text in enumerate(text_content[:10]):  # Show first 10 text parts
    if text.strip():  # Only show non-empty text
        print(f'{i+1}. {text[:300]}...' if len(text) > 300 else f'{i+1}. {text}')
        print()

print(f'TOOL CALLS ({len(tool_calls)} total):')
print('-' * 80)
for i, tool_call in enumerate(tool_calls[:15]):  # Show first 15 tool calls
    tool_name = tool_call['tool']
    status = tool_call['state'].get('status', 'unknown')
    print(f'{i+1}. {tool_name} - {status}')

print()
print(f'REASONING CONTENT ({len(reasoning_content)} total):')
print('-' * 80)
for i, reasoning in enumerate(reasoning_content[:5]):  # Show first 5 reasoning parts
    if reasoning.strip():
        print(f'{i+1}. {reasoning[:200]}...' if len(reasoning) > 200 else f'{i+1}. {reasoning}')
        print()

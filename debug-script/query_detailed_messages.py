import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Check parts table schema
cursor.execute('PRAGMA table_info(part)')
columns = cursor.fetchall()
print('Part table schema:')
for col in columns:
    print(f'  {col[1]}: {col[2]}')
print()

# Get detailed message content for main session
session_id = 'ses_29053f92fffeXHSo1bTDcnMq57'
cursor.execute('''
    SELECT m.id, m.time_created, m.data, p.data 
    FROM message m
    LEFT JOIN part p ON m.id = p.message_id
    WHERE m.session_id = ?
    ORDER BY m.time_created
    LIMIT 20
''', (session_id,))

messages = cursor.fetchall()
print(f'Sample messages from main session ({session_id}):\n')

for msg_id, time_created, msg_data, part_data in messages:
    created = datetime.fromtimestamp(time_created/1000) if time_created else 'N/A'
    print(f'[{created}] Message ID: {msg_id}')
    
    try:
        msg_json = json.loads(msg_data)
        role = msg_json.get('role', 'unknown')
        content = msg_json.get('content', '')
        
        if role == 'user':
            print(f'  [USER] {content[:200]}...' if len(content) > 200 else f'  [USER] {content}')
        elif role == 'assistant':
            print(f'  [ASSISTANT] {content[:200]}...' if len(content) > 200 else f'  [ASSISTANT] {content}')
        else:
            print(f'  [{role}] {content[:200]}...' if len(content) > 200 else f'  [{role}] {content}')
    except:
        print(f'  [RAW] {msg_data[:200]}...' if len(msg_data) > 200 else f'  [RAW] {msg_data}')
    
    if part_data:
        try:
            part_json = json.loads(part_data)
            print(f'  [PART] {str(part_json)[:200]}...' if len(str(part_json)) > 200 else f'  [PART] {part_json}')
        except:
            print(f'  [PART RAW] {part_data[:100]}...')
    
    print()

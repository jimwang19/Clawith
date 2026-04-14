import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Query for specific session IDs
session_ids = [
    'ses_29053f92fffeXHSo1bTDcnMq57',
    'ses_2750fdd88ffeX6uqLO6VwVBBqV', 
    'ses_2750fbee4ffexXOochTgnB6Xh2',
    'ses_27503b1f9ffeDodgDtv5JxGqQL'
]

# Get message counts and summaries for each session
for session_id in session_ids:
    # Get message count
    cursor.execute('SELECT COUNT(*) FROM message WHERE session_id = ?', (session_id,))
    count = cursor.fetchone()[0]
    
    # Get all messages
    cursor.execute('SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created', (session_id,))
    messages = cursor.fetchall()
    
    print(f'Session: {session_id}')
    print(f'Total messages: {count}')
    
    # Try to parse and summarize messages
    for msg_id, time_created, data in messages[:5]:  # Show first 5 messages
        try:
            msg_data = json.loads(data)
            role = msg_data.get('role', 'unknown')
            content = msg_data.get('content', '')
            content_preview = content[:100] + '...' if len(content) > 100 else content
            print(f'  [{role}] {content_preview}')
        except:
            print(f'  [raw data] {data[:100]}...')
    
    if count > 5:
        print(f'  ... and {count - 5} more messages')
    print(f'-' * 60)

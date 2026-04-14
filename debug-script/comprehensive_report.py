import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Get comprehensive session information
session_ids = [
    'ses_29053f92fffeXHSo1bTDcnMq57',
    'ses_2750fdd88ffeX6uqLO6VwVBBqV', 
    'ses_2750fbee4ffexXOochTgnB6Xh2',
    'ses_27503b1f9ffeDodgDtv5JxGqQL',
    'ses_274ff02dfffevcIMDPhkjLGZJ0'
]

print('COMPREHENSIVE SESSION REPORT FOR CLAWITH PROJECT')
print('=' * 80)
print()

for session_id in session_ids:
    # Get session details
    cursor.execute('''
        SELECT id, title, parent_id, directory, time_created, time_updated, 
               summary_additions, summary_deletions, summary_files, 
               summary_diffs, permission, workspace_id
        FROM session 
        WHERE id = ?
    ''', (session_id,))
    
    session = cursor.fetchone()
    if not session:
        print(f'Session {session_id} not found')
        continue
    
    (id, title, parent_id, directory, time_created, time_updated, 
     additions, deletions, files, diffs, permission, workspace_id) = session
    
    created = datetime.fromtimestamp(time_created/1000) if time_created else 'N/A'
    updated = datetime.fromtimestamp(time_updated/1000) if time_updated else 'N/A'
    
    print(f'SESSION: {id}')
    print(f'Title: {title}')
    print(f'Parent: {parent_id}')
    print(f'Directory: {directory}')
    print(f'Workspace: {workspace_id}')
    print(f'Permission: {permission}')
    print(f'Created: {created}')
    print(f'Updated: {updated}')
    print(f'Changes: +{additions} -{deletions} in {files} files')
    
    # Get message count and sample content
    cursor.execute('SELECT COUNT(*) FROM message WHERE session_id = ?', (session_id,))
    msg_count = cursor.fetchone()[0]
    print(f'Total Messages: {msg_count}')
    
    # Get first and last user messages for context
    cursor.execute('''
        SELECT data FROM message 
        WHERE session_id = ? AND data LIKE '%user%'
        ORDER BY time_created 
        LIMIT 1
    ''', (session_id,))
    first_user = cursor.fetchone()
    
    cursor.execute('''
        SELECT data FROM message 
        WHERE session_id = ? AND data LIKE '%user%'
        ORDER BY time_created DESC
        LIMIT 1
    ''', (session_id,))
    last_user = cursor.fetchone()
    
    if first_user:
        try:
            user_data = json.loads(first_user[0])
            content = user_data.get('content', '')
            print(f'First user request: {content[:150]}...' if len(content) > 150 else f'First user request: {content}')
        except:
            pass
    
    if last_user and last_user != first_user:
        try:
            user_data = json.loads(last_user[0])
            content = user_data.get('content', '')
            print(f'Last user request: {content[:150]}...' if len(content) > 150 else f'Last user request: {content}')
        except:
            pass
    
    print()
    print('-' * 80)
    print()

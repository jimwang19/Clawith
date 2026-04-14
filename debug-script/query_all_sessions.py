import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Get ALL sessions for the Clawith project
cursor.execute("""
    SELECT id, title, parent_id, directory, time_created, time_updated, 
           summary_additions, summary_deletions, summary_files, workspace_id
    FROM session 
    WHERE directory LIKE '%Clawith%'
    ORDER BY time_created
""")

all_sessions = cursor.fetchall()
print(f'Total Clawith sessions: {len(all_sessions)}\n')

# Build session tree
session_tree = {}
for session in all_sessions:
    session_id = session[0]
    session_tree[session_id] = {
        'data': session,
        'children': []
    }

# Establish parent-child relationships
for session_id, session_info in session_tree.items():
    parent_id = session_info['data'][2]
    if parent_id and parent_id in session_tree:
        session_tree[parent_id]['children'].append(session_id)

# Print session hierarchy
def print_session_tree(session_id, indent=0):
    if session_id not in session_tree:
        return
    
    session = session_tree[session_id]['data']
    (id, title, parent_id, directory, time_created, time_updated, 
     additions, deletions, files, workspace_id) = session
    
    created = datetime.fromtimestamp(time_created/1000) if time_created else 'N/A'
    
    indent_str = '  ' * indent
    print(f'{indent_str}├─ Session: {id}')
    print(f'{indent_str}   Title: {title}')
    print(f'{indent_str}   Created: {created}')
    print(f'{indent_str}   Changes: +{additions} -{deletions} in {files} files')
    
    # Get message count
    cursor.execute('SELECT COUNT(*) FROM message WHERE session_id = ?', (session_id,))
    msg_count = cursor.fetchone()[0]
    print(f'{indent_str}   Messages: {msg_count}')
    
    for child_id in session_tree[session_id]['children']:
        print_session_tree(child_id, indent + 1)

# Find root sessions (no parent in our dataset)
root_sessions = [sid for sid, info in session_tree.items() if info['data'][2] is None or info['data'][2] not in session_tree]

for root_id in root_sessions:
    print_session_tree(root_id)

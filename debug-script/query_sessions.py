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

# Get sessions for Clawith project
cursor.execute("""
    SELECT id, title, parent_id, directory, time_created, time_updated, 
           summary_additions, summary_deletions, summary_files, workspace_id
    FROM session 
    WHERE directory LIKE '%Clawith%' OR id IN (?, ?, ?, ?)
    ORDER BY time_created
""", session_ids)

sessions = cursor.fetchall()
print(f'Found {len(sessions)} sessions\n')

for session in sessions:
    (id, title, parent_id, directory, time_created, time_updated, 
     additions, deletions, files, workspace_id) = session
    
    created = datetime.fromtimestamp(time_created/1000) if time_created else 'N/A'
    updated = datetime.fromtimestamp(time_updated/1000) if time_updated else 'N/A'
    
    print(f'Session ID: {id}')
    print(f'Title: {title}')
    print(f'Parent: {parent_id}')
    print(f'Directory: {directory}')
    print(f'Workspace: {workspace_id}')
    print(f'Created: {created}')
    print(f'Updated: {updated}')
    print(f'Changes: +{additions} -{deletions} in {files} files')
    print(f'-' * 60)

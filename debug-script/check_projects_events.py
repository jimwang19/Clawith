import sqlite3
from datetime import datetime

conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Get project information for Clawith
cursor.execute(\"SELECT * FROM project WHERE directory LIKE '%Clawith%'\")
projects = cursor.fetchall()

if projects:
    print('Clawith Projects found:')
    print('-' * 80)
    for project in projects:
        print(f'Project ID: {project[0]}')
        print(f'Directory: {project[1]}')
        if project[2]:
            created = datetime.fromtimestamp(project[2]/1000)
            print(f'Created: {created}')
        else:
            print('Created: N/A')
        if project[3]:
            updated = datetime.fromtimestamp(project[3]/1000)
            print(f'Updated: {updated}')
        else:
            print('Updated: N/A')
        print()

# Check events for our sessions
session_ids = [
    'ses_29053f92fffeXHSo1bTDcnMq57',
    'ses_2750fdd88ffeX6uqLO6VwVBBqV', 
    'ses_2750fbee4ffexXOochTgnB6Xh2',
    'ses_27503b1f9ffeDodgDtv5JxGqQL'
]

for session_id in session_ids:
    cursor.execute('SELECT COUNT(*) FROM event WHERE session_id = ?', (session_id,))
    event_count = cursor.fetchone()[0]
    print(f'Session {session_id}: {event_count} events')

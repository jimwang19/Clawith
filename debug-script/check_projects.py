import sqlite3
from datetime import datetime

conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Get all projects
cursor.execute('SELECT * FROM project')
projects = cursor.fetchall()

print('All projects in database:')
print('-' * 80)
for project in projects:
    print(f'Project ID: {project[0]}')
    print(f'Directory: {project[1]}')
    if project[2]:
        created = datetime.fromtimestamp(project[2]/1000)
        print(f'Created: {created}')
    if project[3]:
        updated = datetime.fromtimestamp(project[3]/1000)
        print(f'Updated: {updated}')
    print()

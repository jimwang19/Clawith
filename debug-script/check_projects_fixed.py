import sqlite3
from datetime import datetime

conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Get all projects and look for Clawith
cursor.execute('SELECT * FROM project')
projects = cursor.fetchall()

print('Projects in database:')
print('-' * 80)
for project in projects:
    print(f'Project ID: {project[0]}')
    print(f'Directory: {project[1]}')
    
    # Check if this is the Clawith project
    if 'Clawith' in str(project[1]):
        print('*** THIS IS THE CLAWITH PROJECT ***')
    
    # Handle timestamp conversion
    if project[2]:
        try:
            created = datetime.fromtimestamp(int(project[2])/1000)
            print(f'Created: {created}')
        except:
            print(f'Created: {project[2]}')
    
    if project[3]:
        try:
            updated = datetime.fromtimestamp(int(project[3])/1000)
            print(f'Updated: {updated}')
        except:
            print(f'Updated: {project[3]}')
    
    print()

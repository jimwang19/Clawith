import sqlite3
conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Get schema of session table
cursor.execute("PRAGMA table_info(session)")
columns = cursor.fetchall()
print('Session table schema:')
for col in columns:
    print(f'  {col[1]}: {col[2]} (nullable: {col[3]}, default: {col[4]}, pk: {col[5]})')

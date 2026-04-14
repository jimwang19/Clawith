import sqlite3
conn = sqlite3.connect(r'C:\Users\jimwa\.local\share\opencode\opencode.db')
cursor = conn.cursor()

# Get schema of message table
cursor.execute("PRAGMA table_info(message)")
columns = cursor.fetchall()
print('Message table schema:')
for col in columns:
    print(f'  {col[1]}: {col[2]} (nullable: {col[3]})')

"""Minimal patch for remote auth.py: replace data.username with data.login_identifier in login query."""
path = '/home/ubuntu/Clawith/backend/app/api/auth.py'

with open(path, 'r') as f:
    content = f.read()

old = (
    '    result = await db.execute(\n'
    '        select(User)\n'
    '        .where(User.username == data.username)\n'
    '    )'
)

new = (
    '    result = await db.execute(\n'
    '        select(User)\n'
    '        .where(\n'
    '            (User.username == data.login_identifier) |\n'
    '            (User.email == data.login_identifier)\n'
    '        )\n'
    '    )'
)

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(content)
    print('patched OK')
else:
    print('ERROR: pattern not found')
    # Show context around line 222
    for i, line in enumerate(content.splitlines(), 1):
        if 'data.username' in line or 'data.login_identifier' in line:
            print(f'  line {i}: {line!r}')

import re

path = r'C:\Users\Muneesha\Desktop\Nurofin Executive AI\nurofin-ai-backend\alembic\versions\671ace7a50e2_add_performance_review.py'
with open(path, 'r', encoding='utf-8') as f:
    code = f.read()

# Comment out the alter_column block
target = "op.alter_column('user', 'google_token_expires_at',"
rep = "# " + target

# It spans multiple lines, so let's just use regex or string replace
lines = code.split('\n')
in_alter = False
for i, line in enumerate(lines):
    if "op.alter_column('user', 'google_token_expires_at'" in line:
        in_alter = True
    if in_alter:
        lines[i] = "# " + line
        if "existing_nullable=True)" in line:
            in_alter = False

code = "\n".join(lines)

with open(path, 'w', encoding='utf-8') as f:
    f.write(code)

print("Fixed migration script")

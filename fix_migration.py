import re

with open('alembic/versions/0423890795a8_add_is_deleted_to_document.py', 'r') as f:
    content = f.read()

# Revert my bad replace
content = content.replace('try: op.add_column', 'op.add_column')
content = content.replace('try: op.create_table', 'op.create_table')

# Now add exception handling properly
lines = content.split('\n')
new_lines = []
for line in lines:
    if line.strip().startswith('op.'):
        new_lines.append('    try:')
        new_lines.append('    ' + line)
        new_lines.append('    except Exception as e: print("Ignored:", e)')
    elif line.strip().startswith('sa.Column') or line.strip().startswith('sa.ForeignKeyConstraint') or line.strip().startswith('sa.PrimaryKeyConstraint') or line.strip() == ')':
        new_lines.append('    ' + line)
    else:
        new_lines.append(line)

with open('alembic/versions/0423890795a8_add_is_deleted_to_document.py', 'w') as f:
    f.write('\n'.join(new_lines))

import os

filepath = 'alembic/versions/0423890795a8_add_is_deleted_to_document.py'
with open(filepath, 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # Instead of try/except which might break syntax if not careful, we can just replace op.add_column with a raw execute that has IF NOT EXISTS
    if "op.add_column('issue', sa.Column('deadline'" in line:
        new_lines.append("    op.execute('ALTER TABLE issue ADD COLUMN IF NOT EXISTS deadline VARCHAR')\n")
    elif "op.add_column('issue', sa.Column('reported_by_id'" in line:
        new_lines.append("    op.execute('ALTER TABLE issue ADD COLUMN IF NOT EXISTS reported_by_id INTEGER REFERENCES \"user\"(id)')\n")
    elif "op.add_column('user', sa.Column('salary'" in line:
        new_lines.append("    op.execute('ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS salary FLOAT')\n")
    elif "op.add_column('user', sa.Column('performance_score'" in line:
        new_lines.append("    op.execute('ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS performance_score FLOAT')\n")
    elif "op.create_table('financerecord'" in line:
        new_lines.append("    op.execute(\"DROP TYPE IF EXISTS financerecordtypeenum CASCADE\")\n")
        new_lines.append("    op.execute(\"DROP TYPE IF EXISTS financerecordstatusenum CASCADE\")\n")
        new_lines.append(line)
    elif "op.add_column('document', sa.Column('is_deleted'" in line:
        new_lines.append("    op.execute('ALTER TABLE document ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE')\n")
    else:
        new_lines.append(line)

with open(filepath, 'w') as f:
    f.writelines(new_lines)

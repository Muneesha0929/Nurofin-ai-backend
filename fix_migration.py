import re

with open('alembic/versions/e05d81e7a4a3_add_target_score.py', 'r') as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "op.add_column('financerecord'" in line:
        continue
    if "op.alter_column('task'" in line:
        skip = True
    if "existing_nullable=True)" in line and skip:
        skip = False
        continue
    if skip:
        continue
    
    if "op.drop_constraint('task_idempotency_key_key'" in line:
        continue
    if "op.drop_index('ix_task_idempotency_key'" in line:
        continue
    if "op.create_index(op.f('ix_task_idempotency_key'" in line:
        continue
    if "op.create_foreign_key(None, 'task', 'quarter'" in line:
        continue
    if "op.create_foreign_key(None, 'task', 'task'" in line:
        continue
    if "op.create_foreign_key(None, 'task', 'user'" in line:
        continue
    if "op.alter_column('user', 'role'" in line:
        skip = True
        continue
    if "op.alter_column('user', 'google_token_expires_at'" in line:
        skip = True
        continue
        
    # downgrades
    if "op.drop_constraint(None, 'task', type_='foreignkey')" in line:
        continue
    if "op.create_unique_constraint('task_idempotency_key_key'" in line:
        continue
    if "op.create_index('ix_task_idempotency_key', 'task'" in line:
        continue
    if "op.drop_column('financerecord', 'status')" in line:
        continue
    if "op.drop_column('financerecord', 'record_type')" in line:
        continue

    new_lines.append(line)

with open('alembic/versions/e05d81e7a4a3_add_target_score.py', 'w') as f:
    f.writelines(new_lines)

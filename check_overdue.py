import asyncio
from sqlalchemy import create_engine, text

engine = create_engine("postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2")

with engine.connect() as conn:
    res = conn.execute(text("SELECT id, title, status, deadline, project_id, parent_id, quarter_id FROM task WHERE status != 'completed' AND deadline < '2026-08-25'"))
    tasks = res.fetchall()
    for t in tasks:
        print(t)

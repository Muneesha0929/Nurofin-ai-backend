import psycopg2
conn = psycopg2.connect("postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2")
cur = conn.cursor()
cur.execute("SELECT task_id, action, description, created_at FROM task_history WHERE task_id IN (129, 165) ORDER BY created_at DESC")
for row in cur.fetchall():
    print(row)

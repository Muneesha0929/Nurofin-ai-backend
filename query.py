import psycopg2
conn = psycopg2.connect("postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2")
cursor = conn.cursor()
cursor.execute("SELECT id, title, status, deadline FROM task WHERE deadline < '2026-08-25' AND status != 'completed' LIMIT 1")
print(cursor.fetchone())

import psycopg2
conn = psycopg2.connect("postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2")
cur = conn.cursor()
cur.execute("SELECT updated_at FROM task WHERE id IN (129, 165)")
print(cur.fetchall())

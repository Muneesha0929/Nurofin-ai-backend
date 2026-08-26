import psycopg2
conn = psycopg2.connect("postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2")
cur = conn.cursor()
cur.execute("SELECT id, is_deleted, status FROM task WHERE id=49")
print(cur.fetchall())

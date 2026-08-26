import psycopg2

url = "postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2"
conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'task';")
cols = [r[0] for r in cur.fetchall()]
print(cols)
cur.close()
conn.close()

import psycopg2
conn = psycopg2.connect("postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2")
cursor = conn.cursor()
cursor.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'task'::regclass")
print(cursor.fetchall())

import psycopg2
import datetime

conn = psycopg2.connect("postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2")
cur = conn.cursor()

today = datetime.datetime.now().strftime('%Y-%m-%d')
cur.execute(f"UPDATE task SET is_deleted = False, status = 'completed', actual_completion_date = '{today}' WHERE id IN (129, 165)")
conn.commit()

cur.execute("SELECT id, title, is_deleted, status, actual_completion_date FROM task WHERE id IN (129, 165)")
print(cur.fetchall())

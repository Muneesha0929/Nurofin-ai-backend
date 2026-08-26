import psycopg2
conn = psycopg2.connect("postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2")
cur = conn.cursor()
cur.execute("SELECT id, title, is_deleted, status FROM task WHERE title ILIKE '%Planner Module%Add event time overlap%' OR title ILIKE '%Documents Provided to Bank for VAR%'")
print(cur.fetchall())

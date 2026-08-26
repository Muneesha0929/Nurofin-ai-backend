import psycopg2
import sys
import os

url = "postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2"

try:
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    
    queries = [
        "ALTER TABLE task ADD COLUMN IF NOT EXISTS actual_completion_date VARCHAR;",
        "ALTER TABLE task ADD COLUMN IF NOT EXISTS extended_time FLOAT;",
        "ALTER TABLE task ADD COLUMN IF NOT EXISTS pushed_to_next_day BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE task ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR;"
    ]
    
    for q in queries:
        try:
            cur.execute(q)
            print("Executed:", q)
        except Exception as e:
            print("Error executing:", q, e)
            
    cur.close()
    conn.close()
    print("Done")
except Exception as e:
    print("Connection failed:", e)

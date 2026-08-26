import psycopg2
import sys

url = "postgresql://postgres:Asdfnurofin1234@3.108.153.169/nurofin_db_v2"

try:
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    
    # Terminate idle transactions that might be holding locks
    query = """
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = 'nurofin_db_v2'
      AND pid <> pg_backend_pid()
      AND state in ('idle in transaction', 'idle in transaction (aborted)', 'active');
    """
    cur.execute(query)
    print("Terminated active/idle connections.")
    
    cur.close()
    conn.close()
except Exception as e:
    print("Connection failed:", e)

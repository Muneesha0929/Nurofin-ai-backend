import psycopg

def main():
    DATABASE_URL = "postgresql://nurofin_db_user:nt5CGjgJlmCCWZdTuSUgTs6GwiAWaFAm@dpg-d9cqss61a83c739lrpn0-a.virginia-postgres.render.com/nurofin_db?sslmode=require"
    
    print("Connecting to Render database...")
    try:
        conn = psycopg.connect(DATABASE_URL)
        conn.autocommit = True
        
        with conn.cursor() as cur:
            try:
                # Test User query
                print("Testing User query...")
                cur.execute('SELECT * FROM "user" LIMIT 1;')
                print("User query passed!")
            except Exception as e:
                print(f"User query failed: {e}")
                
            try:
                # Test Project query
                print("Testing Project query...")
                cur.execute('SELECT * FROM project LIMIT 1;')
                print("Project query passed!")
            except Exception as e:
                print(f"Project query failed: {e}")
                
            try:
                # Test Task query
                print("Testing Task query...")
                cur.execute('SELECT * FROM task LIMIT 1;')
                print("Task query passed!")
            except Exception as e:
                print(f"Task query failed: {e}")
                
            try:
                # Test Document query
                print("Testing Document query...")
                cur.execute('SELECT * FROM document LIMIT 1;')
                print("Document query passed!")
            except Exception as e:
                print(f"Document query failed: {e}")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()

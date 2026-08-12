import psycopg

def main():
    DATABASE_URL = "postgresql://nurofin_db_user:nt5CGjgJlmCCWZdTuSUgTs6GwiAWaFAm@dpg-d9cqss61a83c739lrpn0-a.virginia-postgres.render.com/nurofin_db?sslmode=require"
    
    print("Connecting to Render database...")
    try:
        conn = psycopg.connect(DATABASE_URL)
        conn.autocommit = True
        print("Connected! Adding is_deleted to document table...")
        
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE document ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;')
            print("Successfully added is_deleted to document table.")
            
            # Also ensure documentuseraccess has is_deleted, created_at, updated_at
            cur.execute('ALTER TABLE documentuseraccess ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;')
            cur.execute('ALTER TABLE documentuseraccess ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE;')
            cur.execute('ALTER TABLE documentuseraccess ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE;')
            print("Successfully ensured documentuseraccess columns.")
        
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()

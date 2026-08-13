from sqlalchemy import create_engine
from sqlalchemy import text
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
# Patch DATABASE_URL for psycopg2
if DATABASE_URL.startswith('postgresql+asyncpg://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)

def show_locks():
    engine = create_engine(DATABASE_URL, isolation_level='AUTOCOMMIT')
    with engine.connect() as conn:
        try:
            res = conn.execute(text("""
            SELECT pid, state, query
            FROM pg_stat_activity
            WHERE datname = current_database()
            """))
            for r in res:
                print(r)
        except Exception as e: print(e)

show_locks()

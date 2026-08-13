from sqlalchemy import create_engine
from sqlalchemy import text
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
# Patch DATABASE_URL for psycopg2
if DATABASE_URL.startswith('postgresql+asyncpg://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)

def kill_queries():
    engine = create_engine(DATABASE_URL, isolation_level='AUTOCOMMIT')
    with engine.connect() as conn:
        try:
            # Kill all other connections to the database to free locks
            conn.execute(text("""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = current_database()
              AND pid <> pg_backend_pid();
            """))
            print('Killed all other database connections')
        except Exception as e: print(e)

kill_queries()

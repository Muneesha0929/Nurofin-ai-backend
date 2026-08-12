from sqlalchemy import create_engine
from sqlalchemy import text
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
# Patch DATABASE_URL for psycopg2
if DATABASE_URL.startswith('postgresql+asyncpg://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)

def patch():
    # Use isolation_level='AUTOCOMMIT' so we don't have transaction blocks aborting on error
    engine = create_engine(DATABASE_URL, isolation_level='AUTOCOMMIT')
    with engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE "documentuseraccess" ADD COLUMN granted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP'))
            print('Added granted_at to documentuseraccess')
        except Exception as e: print(e)
        
        try:
            conn.execute(text('ALTER TABLE "document" ADD COLUMN is_deleted BOOLEAN DEFAULT FALSE'))
            print('Added is_deleted to document')
        except Exception as e: print(e)

patch()

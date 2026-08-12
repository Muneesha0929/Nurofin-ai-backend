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
            conn.execute(text('ALTER TABLE "user" ADD COLUMN salary FLOAT DEFAULT NULL'))
            print('Added salary')
        except Exception as e: print(e)
        
        try:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN performance_score FLOAT DEFAULT NULL'))
            print('Added performance_score')
        except Exception as e: print(e)
        
        try:
            conn.execute(text('ALTER TABLE "issue" ADD COLUMN deadline VARCHAR DEFAULT NULL'))
            print('Added issue.deadline')
        except Exception as e: print(e)
        
        try:
            conn.execute(text('ALTER TABLE "issue" ADD COLUMN reported_by_id INTEGER DEFAULT NULL'))
            print('Added issue.reported_by_id')
        except Exception as e: print(e)

patch()

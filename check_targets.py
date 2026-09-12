from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()
db_url = os.environ.get("DATABASE_URL").replace("+asyncpg", "")
engine = create_engine(db_url)

with engine.connect() as conn:
    targets = conn.execute(text("SELECT id, title, user_id, is_global FROM target WHERE user_id IN (3, 7) OR created_by_id IN (3, 7)")).fetchall()
    print("Targets found:", targets)

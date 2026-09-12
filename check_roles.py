from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()
db_url = os.environ.get("DATABASE_URL").replace("+asyncpg", "")
engine = create_engine(db_url)

with engine.connect() as conn:
    users = conn.execute(text("SELECT id, username, full_name, role FROM \"user\"")).fetchall()
    print("Users:", users)

from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()
db_url = os.environ.get("DATABASE_URL").replace("+asyncpg", "")
engine = create_engine(db_url)

with engine.begin() as conn:
    conn.execute(text("UPDATE \"user\" SET role = 'team_lead' WHERE full_name = 'Pranesh' OR username = 'Pranesh'"))
print("Updated Pranesh to team_lead")

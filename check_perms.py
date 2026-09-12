from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()
db_url = os.environ.get("DATABASE_URL").replace("+asyncpg", "")
engine = create_engine(db_url)

with engine.connect() as conn:
    # Let's see users
    users = conn.execute(text("SELECT id, username, full_name, email FROM \"user\" WHERE username ILIKE '%Muneesha%' OR full_name ILIKE '%Muneesha%' OR username ILIKE '%Harshini%' OR full_name ILIKE '%Harshini%'")).fetchall()
    print("Users found:", users)
    
    # Let's see permissions
    perms = conn.execute(text("SELECT * FROM target_permission")).fetchall()
    print("Permissions found:", perms)

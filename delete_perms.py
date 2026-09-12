from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()
db_url = os.environ.get("DATABASE_URL").replace("+asyncpg", "")
engine = create_engine(db_url)

with engine.begin() as conn:
    conn.execute(text("DELETE FROM target_permission"))
    print("Permissions deleted")
    # if there are global targets created by Muneesha09 or Harshini, let's delete them.
    conn.execute(text("DELETE FROM target WHERE is_global = True AND created_by_id IN (3, 7)"))
    print("Global targets deleted")

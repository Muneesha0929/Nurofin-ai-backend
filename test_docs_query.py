import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from app.models.document import Document, DocumentUserAccess
from app.models.user import User
from sqlalchemy import select, or_

def _is_ceo(user: User) -> bool:
    role = user.role.value if hasattr(user.role, "value") else (user.role or "employee")
    return role in ("ceo", "admin", "super_admin")

async def main():
    async with SessionLocal() as db:
        try:
            print("Fetching user id=3...")
            result = await db.execute(select(User).filter(User.id == 3))
            current_user = result.scalars().first()
            if not current_user:
                print("User 3 not found")
                return
            
            print(f"Found user 3. CEO: {_is_ceo(current_user)}")
            
            print("Building documents query...")
            access_subquery = select(DocumentUserAccess.document_id).filter(
                DocumentUserAccess.user_id == current_user.id
            )

            query = select(Document).filter(Document.is_deleted == False)

            if not _is_ceo(current_user):
                query = query.filter(
                    or_(
                        Document.uploaded_by_id == current_user.id,
                        Document.id.in_(access_subquery),
                    )
                )

            print("Executing documents query...")
            result = await db.execute(query.order_by(Document.created_at.desc()).offset(0).limit(100))
            docs = result.scalars().all()
            print(f"Success! Found {len(docs)} documents.")
        except Exception as e:
            print(f"Error executing documents query: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())

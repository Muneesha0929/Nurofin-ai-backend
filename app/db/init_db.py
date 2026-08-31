import asyncio
from sqlalchemy.future import select
from sqlalchemy import text
from app.db.base_class import Base
from app.db.session import SessionLocal, engine

# Import all models to ensure they are registered with Base.metadata
from app.models import User, Department, Role
from app.core.security import get_password_hash

# Predefined departments and their roles (matching reset_and_seed.py)
DEPARTMENTS_AND_ROLES = {
    "Executive Board": [
        "CEO"
    ],
    "Human Resources (HR)": [
        "HR Manager",
        "HR Executive",
        "Recruiter",
        "Talent Acquisition Specialist",
        "HR Intern"
    ],
    "Finance & Accounts": [
        "Finance Manager",
        "Accountant",
        "Payroll Executive",
        "Finance Executive",
        "Auditor"
    ],
    "Technology": [
        "Engineering Manager",
        "Technical Lead (Tech Lead)",
        "Software Architect",
        "Senior Software Engineer",
        "Software Engineer",
        "Junior Software Engineer",
        "Full Stack Developer",
        "Frontend Developer",
        "Backend Developer",
        "Mobile App Developer",
        "AI/ML Engineer",
        "Data Engineer",
        "DevOps Engineer",
        "Cloud Engineer",
        "QA/Test Engineer",
        "Automation Test Engineer",
        "Database Administrator (DBA)",
        "UI/UX Designer",
        "Product Designer",
        "Security Engineer",
        "Site Reliability Engineer (SRE)"
    ],
    "Product": [
        "Product Manager",
        "Associate Product Manager",
        "Business Analyst",
        "Product Owner"
    ],
    "Project Management": [
        "Project Manager",
        "Program Manager",
        "Scrum Master",
        "Delivery Manager"
    ],
    "Sales": [
        "Sales Director",
        "Sales Manager",
        "Business Development Manager",
        "Business Development Executive",
        "Sales Executive",
        "Account Manager"
    ],
    "Marketing": [
        "Marketing Manager",
        "Digital Marketing Executive",
        "SEO Specialist",
        "Content Writer",
        "Graphic Designer",
        "Social Media Manager"
    ],
    "Customer Success & Support": [
        "Customer Success Manager",
        "Customer Support Executive",
        "Technical Support Engineer",
        "Help Desk Executive"
    ],
    "Legal & Compliance": [
        "Legal Advisor",
        "Compliance Officer",
        "Information Security Officer"
    ],
    "Operations": [
        "Operations Manager",
        "Operations Executive"
    ],
    "Data & Analytics": [
        "Data Scientist",
        "Data Analyst",
        "Business Intelligence Analyst"
    ],
    "Interns & Trainees": [
        "Software Intern",
        "AI Intern",
        "QA Intern",
        "HR Intern",
        "Marketing Intern",
        "Finance Intern"
    ]
}

# Default permissions for certain roles
ALL_PERMISSIONS = ["read_finance", "write_finance", "edit_tasks", "access_ai", "manage_users"]
FINANCE_PERMISSIONS = ["read_finance", "write_finance", "access_ai"]
TECH_LEAD_PERMISSIONS = ["edit_tasks", "access_ai"]

async def ensure_columns(db) -> None:
    """Add missing columns to existing tables (create_all does not alter tables)."""
    try:
        dialect = engine.dialect.name
        if dialect != "postgresql":
            return
        stmts = [
            "ALTER TABLE issue ADD COLUMN IF NOT EXISTS reported_by_id INTEGER",
            "ALTER TABLE issue ADD COLUMN IF NOT EXISTS deadline VARCHAR",
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS salary FLOAT",
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS performance_score FLOAT",
            "ALTER TABLE notification ADD COLUMN IF NOT EXISTS link VARCHAR",
            "ALTER TABLE issue ADD COLUMN IF NOT EXISTS scheduled_date VARCHAR",
            "ALTER TABLE issue ADD COLUMN IF NOT EXISTS actual_completion_date VARCHAR",
        ]
        for stmt in stmts:
            await db.execute(text(stmt))
        await db.commit()
    except Exception as e:
        print(f"Database Initialization: Column sync skipped: {e}")
        await db.rollback()


async def init_db() -> None:
    """
    Automatically creates all tables and seeds default data (CEO user and roles)
    if the database is empty or not seeded.
    """
    print("Database Initialization: Verifying database tables...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("Database Initialization: Tables verified/created successfully.")
    except Exception as e:
        print(f"Database Initialization: Error verifying/creating tables: {e}")
        return

    async with SessionLocal() as db:
        try:
            await ensure_columns(db)
            admin_email = "vincent@nurofin.com"
            result = await db.execute(select(User).filter(User.email == admin_email))
            user = result.scalars().first()
            
            if not user:
                print("Database Initialization: CEO user not found. Seeding default data...")
                
                # Check departments
                result_dept = await db.execute(select(Department))
                depts = result_dept.scalars().all()
                
                if not depts:
                    print("Database Initialization: Seeding departments and roles...")
                    for dept_name, roles_list in DEPARTMENTS_AND_ROLES.items():
                        dept = Department(name=dept_name, is_custom=False)
                        db.add(dept)
                        await db.flush() # Flush to get dept.id
                        
                        for role_name in roles_list:
                            perms = ["access_ai"]
                            if role_name == "CEO":
                                perms = ALL_PERMISSIONS
                            elif "Finance" in role_name or role_name == "Accountant" or role_name == "Auditor":
                                perms = FINANCE_PERMISSIONS
                            elif role_name in ["Engineering Manager", "Technical Lead (Tech Lead)", "Software Architect", "Project Manager", "Product Manager", "Product Owner"]:
                                perms = TECH_LEAD_PERMISSIONS
                                
                            role = Role(
                                name=role_name,
                                department_id=dept.id,
                                permissions=perms,
                                is_custom=False
                            )
                            db.add(role)
                
                # Seed Vincent as CEO/Admin User
                admin_user = User(
                    email=admin_email,
                    full_name="Vincent CEO",
                    username="vincent_ceo",
                    role="ceo",
                    department="Executive Board",
                    phone="+1 (555) 019-2834",
                    github="https://github.com/vincent-nurofin",
                    linkedin="https://linkedin.com/in/vincent-nurofin",
                    profile_picture="https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?auto=format&fit=crop&w=256&h=256&q=80",
                    hashed_password=get_password_hash("qwerty"),
                    is_active=True
                )
                db.add(admin_user)
                await db.commit()
                print("Database Initialization: Seeded CEO user and departments successfully.")
            else:
                print("Database Initialization: CEO user already exists. Seeding skipped.")
        except Exception as e:
            print(f"Database Initialization: Error seeding database: {e}")
            await db.rollback()

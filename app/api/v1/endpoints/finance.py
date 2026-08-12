from typing import Any, Optional
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func

from app.api import deps
from app.models.finance_record import FinanceRecord, FinanceRecordTypeEnum, FinanceRecordStatusEnum
from app.models.performance_review import PerformanceReview
from app.models.project import Project
from app.models.quarter import Quarter
from app.models.user import User
from app.models.notification import Notification, NotificationTypeEnum
from app.schemas.finance import FinanceRecordCreate, FinanceRecordUpdate, PerformanceReviewCreate
from app.core.responses import APIResponse, success_response, error_response

router = APIRouter()

CEO_ROLES = ("ceo", "admin", "super_admin")


def _role(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else (user.role or "employee")


def _is_ceo(user: User) -> bool:
    return _role(user) in CEO_ROLES


def _fmt_date(d) -> Optional[str]:
    if d is None:
        return None
    if hasattr(d, 'isoformat'):
        return d.isoformat()
    return str(d)


def _serialize_record(db: AsyncSession, r: FinanceRecord) -> dict:
    return {
        "id": r.id,
        "title": r.title,
        "description": r.description,
        "record_type": r.record_type.value if hasattr(r.record_type, "value") else r.record_type,
        "status": r.status.value if hasattr(r.status, "value") else r.status,
        "amount": r.amount,
        "currency": r.currency,
        "vendor": r.vendor,
        "department": r.department,
        "cost_category": r.cost_category,
        "project_id": r.project_id,
        "project_name": r.project.name if r.project else None,
        "user_id": r.user_id,
        "user_name": r.user.full_name if r.user else None,
        "due_date": r.due_date,
        "transaction_date": r.transaction_date,
        "notes": r.notes,
        "created_by_id": r.created_by_id,
        "created_at": _fmt_date(r.created_at),
    }


async def _serialize_review(r: PerformanceReview) -> dict:
    return {
        "id": r.id,
        "user_id": r.user_id,
        "user_name": r.user.full_name if r.user else None,
        "user_avatar": r.user.profile_picture if r.user else None,
        "user_role": r.user.role if r.user else None,
        "user_department": r.user.department if r.user else None,
        "quarter_id": r.quarter_id,
        "quarter_name": r.quarter.name if r.quarter else None,
        "score": r.score,
        "rating": r.rating,
        "comments": r.comments,
        "salary_before": r.salary_before,
        "salary_after": r.salary_after,
        "increment_pct": r.increment_pct,
        "reviewed_by_id": r.reviewed_by_id,
        "reviewed_by_name": r.reviewed_by.full_name if r.reviewed_by else None,
        "created_at": _fmt_date(r.created_at),
    }


def _compute_rating(score: float) -> tuple[str, float]:
    if score >= 90:
        return "Outstanding", 15.0
    if score >= 80:
        return "Excellent", 10.0
    if score >= 70:
        return "Good", 7.0
    if score >= 60:
        return "Satisfactory", 5.0
    if score >= 50:
        return "Needs Improvement", 2.0
    return "Underperforming", 0.0


@router.get("", response_model=APIResponse)
async def read_finance_records(
    db: AsyncSession = Depends(deps.get_db),
    record_type: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 200,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    q = select(FinanceRecord).options(
        selectinload(FinanceRecord.project),
        selectinload(FinanceRecord.user),
    ).filter(FinanceRecord.is_deleted == False)

    if record_type:
        q = q.filter(FinanceRecord.record_type == record_type)
    if status:
        q = q.filter(FinanceRecord.status == status)
    if project_id:
        q = q.filter(FinanceRecord.project_id == project_id)
    if user_id:
        q = q.filter(FinanceRecord.user_id == user_id)

    result = await db.execute(q.order_by(FinanceRecord.created_at.desc()).offset(skip).limit(limit))
    records = result.scalars().all()
    data = [_serialize_record(db, r) for r in records]

    total = (await db.execute(
        select(func.count()).select_from(FinanceRecord).filter(FinanceRecord.is_deleted == False)
    )).scalar() or 0

    return success_response(data={"records": data, "total": total}, message="Finance records fetched successfully")


@router.get("/summary", response_model=APIResponse)
async def finance_summary(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    all_records = (await db.execute(
        select(FinanceRecord).filter(FinanceRecord.is_deleted == False)
    )).scalars().all()

    budgets = sum(r.amount for r in all_records if r.record_type in (FinanceRecordTypeEnum.budget, "budget"))
    expenses = sum(r.amount for r in all_records if r.record_type in (FinanceRecordTypeEnum.expense, "expense"))
    vendor = sum(r.amount for r in all_records if r.record_type in (FinanceRecordTypeEnum.vendor_payment, "vendor_payment"))
    renewals = sum(r.amount for r in all_records if r.record_type in (FinanceRecordTypeEnum.renewal, "renewal"))
    salaries = sum(r.amount for r in all_records if r.record_type in (FinanceRecordTypeEnum.salary, "salary"))
    revenue = sum(r.amount for r in all_records if r.record_type in (FinanceRecordTypeEnum.revenue, "revenue"))
    other = sum(r.amount for r in all_records if r.record_type in (FinanceRecordTypeEnum.other, "other"))

    pending = sum(r.amount for r in all_records
                  if r.status in (FinanceRecordStatusEnum.pending, "pending"))
    overdue = sum(r.amount for r in all_records
                  if r.status in (FinanceRecordStatusEnum.overdue, "overdue"))
    approved = sum(r.amount for r in all_records
                   if r.status in (FinanceRecordStatusEnum.approved, "approved"))
    paid = sum(r.amount for r in all_records
               if r.status in (FinanceRecordStatusEnum.paid, "paid"))

    project_budgets = sum(p.budget or 0 for p in
                          (await db.execute(select(Project).filter(Project.is_deleted == False))).scalars().all())

    total_budget = budgets + project_budgets
    total_spend = expenses + vendor + renewals + other
    budget_remaining = total_budget - total_spend - salaries

    return success_response(data={
        "total_budget": round(total_budget, 2),
        "project_budgets": round(project_budgets, 2),
        "allocated_budget": round(budgets, 2),
        "total_expenses": round(expenses, 2),
        "total_vendor_payments": round(vendor, 2),
        "total_renewals": round(renewals, 2),
        "total_salaries": round(salaries, 2),
        "total_revenue": round(revenue, 2),
        "other_spending": round(other, 2),
        "total_spend": round(total_spend, 2),
        "pending_payments": round(pending, 2),
        "overdue_payments": round(overdue, 2),
        "approved_payments": round(approved, 2),
        "paid_payments": round(paid, 2),
        "budget_remaining": round(budget_remaining, 2),
    }, message="Finance summary fetched successfully")


@router.get("/projects/budget", response_model=APIResponse)
async def project_budget_breakdown(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Project).filter(Project.is_deleted == False).order_by(Project.name.asc()))
    projects = result.scalars().all()
    data = []
    for p in projects:
        data.append({
            "id": p.id,
            "name": p.name,
            "status": p.status.value if hasattr(p.status, "value") else p.status,
            "budget": p.budget or 0.0,
            "spending": p.spending or 0.0,
            "remaining": round((p.budget or 0.0) - (p.spending or 0.0), 2),
        })
    return success_response(data=data, message="Project budget breakdown fetched successfully")


@router.get("/performance", response_model=APIResponse)
async def read_performance_reviews(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(PerformanceReview)
        .options(
            selectinload(PerformanceReview.user),
            selectinload(PerformanceReview.reviewed_by),
            selectinload(PerformanceReview.quarter),
        )
        .filter(PerformanceReview.is_deleted == False)
        .order_by(PerformanceReview.created_at.desc())
    )
    reviews = [await _serialize_review(r) for r in result.scalars().all()]

    # Include current user's salary & latest score for self-service view
    mine = None
    if current_user:
        mine = {
            "user_id": current_user.id,
            "name": current_user.full_name,
            "salary": current_user.salary or 0.0,
            "performance_score": current_user.performance_score or 0.0,
        }
    return success_response(data={"reviews": reviews, "mine": mine}, message="Performance reviews fetched successfully")


@router.post("/performance/review", response_model=APIResponse)
async def create_performance_review(
    *,
    db: AsyncSession = Depends(deps.get_db),
    review_in: PerformanceReviewCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if not _is_ceo(current_user):
        return error_response(message="Only the CEO or super admin can review performance")

    user = (await db.execute(select(User).filter(User.id == review_in.user_id, User.is_deleted == False))).scalars().first()
    if not user:
        return error_response(message="User not found")

    score = max(0.0, min(100.0, float(review_in.score)))
    rating, auto_increment = _compute_rating(score)
    increment = float(review_in.increment_pct) if review_in.increment_pct is not None else auto_increment

    salary_before = user.salary or 0.0
    salary_after = round(salary_before * (1 + increment / 100.0), 2) if increment > 0 else salary_before

    # Check for existing review for this user/quarter
    existing = (await db.execute(
        select(PerformanceReview).filter(
            PerformanceReview.user_id == review_in.user_id,
            PerformanceReview.quarter_id == review_in.quarter_id,
            PerformanceReview.is_deleted == False,
        )
    )).scalars().first()

    if existing:
        existing.score = score
        existing.rating = rating
        existing.comments = review_in.comments
        existing.salary_before = salary_before
        existing.salary_after = salary_after
        existing.increment_pct = increment
        existing.reviewed_by_id = current_user.id
        db_review = existing
    else:
        db_review = PerformanceReview(
            user_id=review_in.user_id,
            quarter_id=review_in.quarter_id,
            score=score,
            rating=rating,
            comments=review_in.comments,
            salary_before=salary_before,
            salary_after=salary_after,
            increment_pct=increment,
            reviewed_by_id=current_user.id,
        )
        db.add(db_review)

    # Sync to user profile
    user.performance_score = score
    user.salary = salary_after
    db.add(user)

    db.add(Notification(
        title="Performance review completed",
        message=f"{current_user.full_name or 'CEO'} updated your performance to {score:.0f}/100 — salary is now ${salary_after:,.2f}.",
        type=NotificationTypeEnum.performance_reviewed,
        user_id=review_in.user_id,
        link="/finance",
    ))

    await db.commit()
    await db.refresh(db_review)
    return success_response(data=await _serialize_review(db_review), message="Performance review saved")


@router.post("", response_model=APIResponse)
async def create_finance_record(
    *,
    db: AsyncSession = Depends(deps.get_db),
    record_in: FinanceRecordCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if not _is_ceo(current_user):
        return error_response(message="Only the CEO or super admin can add finance records")

    data = record_in.dict(exclude_unset=True)

    if data.get("project_id"):
        project = (await db.execute(select(Project).filter(Project.id == data["project_id"], Project.is_deleted == False))).scalars().first()
        if not project:
            return error_response(message="Project not found")
    if data.get("user_id"):
        user = (await db.execute(select(User).filter(User.id == data["user_id"], User.is_deleted == False))).scalars().first()
        if not user:
            return error_response(message="User not found")

    db_record = FinanceRecord(**data, created_by_id=current_user.id)
    db.add(db_record)
    await db.flush()

    # Sync project budget/spending when a record references a project
    if data.get("project_id"):
        project = (await db.execute(select(Project).filter(Project.id == data["project_id"]))).scalars().first()
        if project:
            record_type = data.get("record_type")
            if record_type == "budget":
                project.budget = data.get("amount", project.budget or 0)
            elif record_type in ("expense", "vendor_payment"):
                project.spending = (project.spending or 0) + data.get("amount", 0)
            db.add(project)

    # When a salary record is added, sync the employee's salary
    if data.get("record_type") == "salary" and data.get("user_id"):
        user = (await db.execute(select(User).filter(User.id == data["user_id"]))).scalars().first()
        if user:
            user.salary = data.get("amount", user.salary or 0)
            db.add(user)

    await db.commit()
    loaded = (await db.execute(
        select(FinanceRecord).options(selectinload(FinanceRecord.project), selectinload(FinanceRecord.user))
        .filter(FinanceRecord.id == db_record.id)
    )).scalars().first()
    return success_response(data=_serialize_record(db, loaded), message="Finance record created")


@router.put("/{record_id}", response_model=APIResponse)
async def update_finance_record(
    record_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    record_in: FinanceRecordUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if not _is_ceo(current_user):
        return error_response(message="Only the CEO or super admin can edit finance records")

    result = await db.execute(
        select(FinanceRecord).options(selectinload(FinanceRecord.project), selectinload(FinanceRecord.user))
        .filter(FinanceRecord.id == record_id, FinanceRecord.is_deleted == False)
    )
    db_record = result.scalars().first()
    if not db_record:
        return error_response(message="Finance record not found")

    update_data = record_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_record, field, value)
    await db.flush()

    if db_record.project_id:
        project = (await db.execute(select(Project).filter(Project.id == db_record.project_id))).scalars().first()
        if project:
            record_type = db_record.record_type.value if hasattr(db_record.record_type, "value") else db_record.record_type
            if record_type == "budget":
                project.budget = db_record.amount
            db.add(project)

    if db_record.record_type in (FinanceRecordTypeEnum.salary, "salary") and db_record.user_id:
        user = (await db.execute(select(User).filter(User.id == db_record.user_id))).scalars().first()
        if user:
            user.salary = db_record.amount
            db.add(user)

    await db.commit()
    loaded = (await db.execute(
        select(FinanceRecord).options(selectinload(FinanceRecord.project), selectinload(FinanceRecord.user))
        .filter(FinanceRecord.id == record_id)
    )).scalars().first()
    return success_response(data=_serialize_record(db, loaded), message="Finance record updated")


@router.delete("/{record_id}", response_model=APIResponse)
async def delete_finance_record(
    record_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if not _is_ceo(current_user):
        return error_response(message="Only the CEO or super admin can delete finance records")
    result = await db.execute(
        select(FinanceRecord).filter(FinanceRecord.id == record_id, FinanceRecord.is_deleted == False)
    )
    db_record = result.scalars().first()
    if not db_record:
        return error_response(message="Finance record not found")
    db_record.is_deleted = True
    await db.commit()
    return success_response(message="Finance record deleted")


@router.get("/tracker", response_model=APIResponse)
async def finance_tracker(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(FinanceRecord)
        .options(selectinload(FinanceRecord.project), selectinload(FinanceRecord.user))
        .filter(FinanceRecord.is_deleted == False)
    )
    records = result.scalars().all()

    vendor_payments = []
    salaries = []
    renewals = []
    cloud_costs = []
    office_expenses = []
    budget_commitments = []
    outstanding_invoices = []
    alerts = []
    upcoming_total = 0.0
    overdue_total = 0.0

    today = datetime.utcnow().date()

    def _parse_date(s: Optional[str]):
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def _urgency(due, status_val):
        if status_val in ("paid", "approved"):
            return "normal"
        d = _parse_date(due)
        if not d:
            return "normal"
        delta = (d - today).days
        if delta < 0:
            return "overdue"
        if delta <= 3:
            return "critical"
        if delta <= 7:
            return "warning"
        return "normal"

    def _item(r):
        s = _serialize_record(db, r)
        s["_urgency"] = _urgency(r.due_date, s.get("status"))
        return s

    for r in records:
        s = _item(r)
        rt = s.get("record_type")
        status_val = s.get("status")
        due = r.due_date or ""
        due_date = _parse_date(due)
        amt = r.amount or 0.0

        if rt == "vendor_payment":
            vendor_payments.append(s)
        elif rt == "salary":
            salaries.append(s)
        elif rt == "renewal":
            renewals.append(s)
        elif rt == "expense":
            cc = (r.cost_category or "").lower()
            if cc in ("cloud",):
                cloud_costs.append(s)
            else:
                office_expenses.append(s)
        elif rt == "budget":
            budget_commitments.append(s)

        if status_val in ("pending", "approved", "overdue") and due_date is not None:
            delta = (due_date - today).days
            upcoming_total += amt
            if due_date < today:
                overdue_total += amt
            if status_val in ("pending", "approved"):
                if delta < 0:
                    alerts.append({
                        "severity": "overdue",
                        "record_id": r.id,
                        "title": r.title,
                        "record_type": rt,
                        "amount": amt,
                        "currency": r.currency or "USD",
                        "due_date": due,
                        "days_overdue": abs(delta),
                    })
                elif delta <= 7:
                    alerts.append({
                        "severity": "upcoming",
                        "record_id": r.id,
                        "title": r.title,
                        "record_type": rt,
                        "amount": amt,
                        "currency": r.currency or "USD",
                        "due_date": due,
                        "days_remaining": delta,
                    })

        if status_val in ("pending", "overdue"):
            outstanding_invoices.append(s)

    def _sort(items):
        return sorted(items, key=lambda x: x.get("due_date") or "9999-12-31")

    alerts.sort(key=lambda a: (0 if a["severity"] == "overdue" else 1, a.get("due_date") or "9999-12-31"))

    return success_response(data={
        "vendor_payments": _sort(vendor_payments),
        "salaries": _sort(salaries),
        "renewals": _sort(renewals),
        "cloud_costs": _sort(cloud_costs),
        "office_expenses": _sort(office_expenses),
        "budget_commitments": _sort(budget_commitments),
        "outstanding_invoices": _sort(outstanding_invoices),
        "alerts": alerts,
        "summary": {
            "upcoming_payments_total": round(upcoming_total, 2),
            "overdue_total": round(overdue_total, 2),
            "vendor_payments_count": len(vendor_payments),
            "salaries_count": len(salaries),
            "renewals_count": len(renewals),
            "cloud_costs_count": len(cloud_costs),
            "office_expenses_count": len(office_expenses),
            "budget_commitments_count": len(budget_commitments),
            "outstanding_invoices_count": len(outstanding_invoices),
            "alerts_count": len(alerts),
        },
    }, message="Finance tracker fetched successfully")

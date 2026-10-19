"""Runway: money in, money out, how long the cash lasts and when the company breaks even.

CEO only. Every route checks it, reads included: salaries and capital live here, so a
super_admin or admin account gets 403 like everyone else. RUNWAY_ALLOWED_EMAILS (comma
separated) narrows it further to named CEO accounts when set.

The page loads everything with one GET and refetches after each change; the data is small
(a few hundred rows a year), so this keeps the frontend simple and always consistent.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core.config import settings
from app.core.responses import APIResponse, success_response
from app.models.runway import (
    BankCheck, CapitalEntry, Expense, ExpenseCategory, ExpenseMonth,
    Product, ProductFigure, ProductMonth, RunwaySettings,
)
from app.models.user import User
from app.schemas.runway import (
    BankIn, CapitalIn, CategoryIn, ExpenseIn, FigureIn, FillMonth, MonthAmount,
    ProductIn, ProductMonthIn, SettingsIn, MONTH,
)
from app.services import runway_forecast as rf
from app.services import runway_import as ri

router = APIRouter()
IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_CATEGORIES = ["IT", "Admin", "Compliance"]


# ----------------------------------------------------------------- access

def _role(user: User) -> str:
    role = getattr(user, "role", "") or ""
    return str(role.value if hasattr(role, "value") else role).lower()


def can_view(user: User) -> bool:
    if _role(user) != "ceo":
        return False
    allowed = [e.strip().lower() for e in (getattr(settings, "RUNWAY_ALLOWED_EMAILS", "") or "").split(",") if e.strip()]
    return not allowed or (user.email or "").lower() in allowed


async def ceo_only(current_user: User = Depends(deps.get_current_user)) -> User:
    if not can_view(current_user):
        raise HTTPException(status_code=403, detail="Only the CEO can see Runway.")
    return current_user


def today() -> date:
    fixed = (getattr(settings, "RUNWAY_TODAY", "") or "").strip()   # local testing only
    return date.fromisoformat(fixed) if fixed else datetime.now(IST).date()


def cur_month() -> str:
    return today().isoformat()[:7]


def num(v) -> Optional[float]:
    return None if v is None else float(v)


# ----------------------------------------------------------------- loading

async def get_settings(db: AsyncSession) -> RunwaySettings:
    row = (await db.execute(select(RunwaySettings).filter(RunwaySettings.is_deleted == False))).scalars().first()
    if row:
        return row
    # First visit: settings, three starter categories and GMDI, all editable or removable.
    row = RunwaySettings(start_month="2025-12", buffer_pct=5, green_months=12, amber_months=6)
    db.add(row)
    for i, n in enumerate(DEFAULT_CATEGORIES):
        db.add(ExpenseCategory(name=n, sort_order=i))
    db.add(Product(name="GMDI", tracking="figures", cost_label="Bank charges", amount=0, start_month="2025-12", sort_order=0))
    await db.commit()
    await db.refresh(row)
    return row


async def alive(db: AsyncSession, model, *filters):
    return (await db.execute(select(model).filter(model.is_deleted == False, *filters))).scalars().all()


async def load(db: AsyncSession) -> Dict[str, Any]:
    s = await get_settings(db)
    cats = sorted(await alive(db, ExpenseCategory), key=lambda c: (c.sort_order, c.id))
    exps = await alive(db, Expense)
    em = await alive(db, ExpenseMonth)
    prods = sorted(await alive(db, Product), key=lambda p: (p.sort_order, p.id))
    figs = await alive(db, ProductFigure)
    pms = await alive(db, ProductMonth)
    caps = await alive(db, CapitalEntry)
    bank = sorted(await alive(db, BankCheck), key=lambda b: (b.day, b.id))
    live_exp = {e.id for e in exps}
    live_prod = {p.id for p in prods}
    months_by_exp: Dict[int, Dict[str, float]] = {}
    for r in em:
        if r.expense_id in live_exp:
            months_by_exp.setdefault(r.expense_id, {})[r.month] = float(r.amount)
    return {
        "settings": {"start_month": s.start_month, "buffer_pct": float(s.buffer_pct), "green_months": s.green_months, "amber_months": s.amber_months},
        "categories": [{"id": c.id, "name": c.name, "sort_order": c.sort_order} for c in cats],
        "expenses": [{
            "id": e.id, "category_id": e.category_id, "name": e.name, "how": e.how, "amount": float(e.amount),
            "start": e.start_month, "end": e.end_month, "raise": float(e.raise_pct or 0),
            "months": months_by_exp.get(e.id, {}),
        } for e in sorted(exps, key=lambda e: e.id)],
        "products": [{
            "id": p.id, "name": p.name, "tracking": p.tracking, "cost_label": p.cost_label, "amount": float(p.amount),
            "start": p.start_month, "end": p.end_month,
            "figures": sorted([{"id": f.id, "day": f.day, "revenue": float(f.revenue), "cost": num(f.cost), "forecast": num(f.forecast)}
                               for f in figs if f.product_id == p.id], key=lambda f: (f["day"], f["id"])),
            "months": {m.month: {"revenue": float(m.revenue), "cost": float(m.cost or 0)} for m in pms if m.product_id == p.id},
        } for p in prods if p.id in live_prod],
        "capital": sorted([{"id": c.id, "day": c.day, "amount": float(c.amount), "note": c.note} for c in caps], key=lambda c: c["day"]),
        "bank": {"day": bank[-1].day, "amount": float(bank[-1].amount)} if bank else None,
    }


async def everything(db: AsyncSession) -> Dict[str, Any]:
    data = await load(db)
    return {"data": data, "summary": rf.build(data, today())}


async def get_or_404(db: AsyncSession, model, id_: int, what: str):
    row = (await db.execute(select(model).filter(model.id == id_, model.is_deleted == False))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"{what} not found.")
    return row


def check_month(m: str) -> None:
    import re
    if not re.match(MONTH, m):
        raise HTTPException(status_code=400, detail="Month must look like 2026-10.")


# ----------------------------------------------------------------- read

@router.get("", response_model=APIResponse)
async def get_runway(db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    return success_response(data=await everything(db))


@router.get("/glance", response_model=APIResponse)
async def glance(db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    """The few facts the Overview card shows. Same numbers as the Runway page."""
    s = (await everything(db))["summary"]
    keep = ("state", "current_month", "cash_now", "now", "expected", "slower", "need", "last_figure_day", "today", "todo")
    return success_response(data={k: s[k] for k in keep})


# ----------------------------------------------------------------- categories

@router.post("/categories", response_model=APIResponse)
async def create_category(body: CategoryIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    n = len(await alive(db, ExpenseCategory))
    db.add(ExpenseCategory(name=body.name.strip(), sort_order=body.sort_order if body.sort_order is not None else n))
    await db.commit()
    return success_response(data=await everything(db), message="Category added")


@router.put("/categories/{cid}", response_model=APIResponse)
async def update_category(cid: int, body: CategoryIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    c = await get_or_404(db, ExpenseCategory, cid, "Category")
    c.name = body.name.strip()
    if body.sort_order is not None:
        c.sort_order = body.sort_order
    await db.commit()
    return success_response(data=await everything(db), message="Category saved")


@router.delete("/categories/{cid}", response_model=APIResponse)
async def delete_category(cid: int, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    c = await get_or_404(db, ExpenseCategory, cid, "Category")
    if await alive(db, Expense, Expense.category_id == cid):
        raise HTTPException(status_code=409, detail="Move or delete this category's expenses first.")
    c.is_deleted = True
    await db.commit()
    return success_response(data=await everything(db), message="Category deleted")


# ----------------------------------------------------------------- expenses

async def resolve_category(db: AsyncSession, body: ExpenseIn) -> int:
    if body.new_category:
        c = ExpenseCategory(name=body.new_category.strip(), sort_order=len(await alive(db, ExpenseCategory)))
        db.add(c)
        await db.flush()
        return c.id
    if not body.category_id:
        raise HTTPException(status_code=400, detail="Pick a category.")
    await get_or_404(db, ExpenseCategory, body.category_id, "Category")
    return body.category_id


async def set_month(db: AsyncSession, expense_id: int, month: str, amount: Optional[float]) -> None:
    row = (await db.execute(select(ExpenseMonth).filter(ExpenseMonth.expense_id == expense_id, ExpenseMonth.month == month))).scalars().first()
    if amount is None:
        if row:
            await db.delete(row)
        return
    if row:
        row.amount = amount
        row.is_deleted = False
    else:
        db.add(ExpenseMonth(expense_id=expense_id, month=month, amount=amount))


async def apply_expense(db: AsyncSession, e: Expense, body: ExpenseIn, is_new: bool) -> None:
    s = await get_settings(db)
    cur = cur_month()
    category_id = await resolve_category(db, body)   # queries first: a pending new row must not autoflush half-filled
    e.name, e.how, e.category_id = body.name.strip(), body.how, category_id
    if is_new:
        db.add(e)
    if body.how == "once":
        if not body.once_month or body.once_amount is None:
            raise HTTPException(status_code=400, detail="Enter the amount and the month it is paid.")
        e.amount, e.start_month, e.end_month, e.raise_pct = 0, s.start_month, None, 0
        await db.flush()
        if not is_new:   # moving a planned one-off: drop its planned months, keep what was already paid
            await db.execute(sa_delete(ExpenseMonth).where(ExpenseMonth.expense_id == e.id, ExpenseMonth.month >= cur))
        await set_month(db, e.id, body.once_month, body.once_amount)
        return
    start = body.start_month or cur
    if body.end_month and body.end_month < start:
        raise HTTPException(status_code=400, detail="The end month is before the start month.")
    e.amount, e.start_month, e.end_month = body.amount, start, body.end_month
    e.raise_pct = body.raise_pct if body.how == "monthly" else 0
    await db.flush()
    if not is_new:   # a new monthly amount replaces one-month changes from this month on
        await db.execute(sa_delete(ExpenseMonth).where(ExpenseMonth.expense_id == e.id, ExpenseMonth.month >= cur))


@router.post("/expenses", response_model=APIResponse)
async def create_expense(body: ExpenseIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    e = Expense(name=body.name, how=body.how, amount=0, start_month=cur_month())
    await apply_expense(db, e, body, True)
    await db.commit()
    return success_response(data=await everything(db) | {"id": e.id}, message="Expense added")


@router.put("/expenses/{eid}", response_model=APIResponse)
async def update_expense(eid: int, body: ExpenseIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    e = await get_or_404(db, Expense, eid, "Expense")
    await apply_expense(db, e, body, False)
    await db.commit()
    return success_response(data=await everything(db), message="Expense saved")


@router.delete("/expenses/{eid}", response_model=APIResponse)
async def delete_expense(eid: int, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    e = await get_or_404(db, Expense, eid, "Expense")
    e.is_deleted = True
    await db.commit()
    return success_response(data=await everything(db), message="Expense deleted")


@router.put("/expenses/{eid}/months/{month}", response_model=APIResponse)
async def set_expense_month(eid: int, month: str, body: MonthAmount, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    check_month(month)
    await get_or_404(db, Expense, eid, "Expense")
    await set_month(db, eid, month, body.amount)
    await db.commit()
    return success_response(data=await everything(db), message="Saved")


@router.post("/expenses/fill-month", response_model=APIResponse)
async def fill_month(body: FillMonth, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    """Copy the previous month's figures into this month (only the empty ones, by default)."""
    prev = rf.add_m(body.month, -1)
    data = await load(db)
    n = 0
    for e in data["expenses"]:
        if e["how"] == "once" or prev not in e["months"]:
            continue
        if body.only_missing and (body.month in e["months"] or not rf.expense_active(e, body.month)):
            continue
        await set_month(db, e["id"], body.month, e["months"][prev])
        n += 1
    await db.commit()
    return success_response(data=await everything(db) | {"filled": n}, message=f"Filled {n}")


# ----------------------------------------------------------------- products

@router.post("/products", response_model=APIResponse)
async def create_product(body: ProductIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    if body.end_month and body.end_month < body.start_month:
        raise HTTPException(status_code=400, detail="The end month is before the start month.")
    p = Product(name=body.name.strip(), tracking=body.tracking, cost_label=(body.cost_label or "").strip() or None,
                amount=body.amount if body.tracking == "fixed" else 0, start_month=body.start_month, end_month=body.end_month,
                sort_order=len(await alive(db, Product)))
    db.add(p)
    await db.commit()
    return success_response(data=await everything(db) | {"id": p.id}, message="Product added")


@router.put("/products/{pid}", response_model=APIResponse)
async def update_product(pid: int, body: ProductIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    p = await get_or_404(db, Product, pid, "Product")
    if body.end_month and body.end_month < body.start_month:
        raise HTTPException(status_code=400, detail="The end month is before the start month.")
    p.name, p.tracking = body.name.strip(), body.tracking
    p.cost_label = (body.cost_label or "").strip() or None
    p.amount = body.amount if body.tracking == "fixed" else 0
    p.start_month, p.end_month = body.start_month, body.end_month
    await db.commit()
    return success_response(data=await everything(db), message="Product saved")


@router.delete("/products/{pid}", response_model=APIResponse)
async def delete_product(pid: int, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    p = await get_or_404(db, Product, pid, "Product")
    p.is_deleted = True
    await db.commit()
    return success_response(data=await everything(db), message="Product deleted")


async def usual_cost_ratio(db: AsyncSession, pid: int) -> Optional[float]:
    figs = sorted(await alive(db, ProductFigure, ProductFigure.product_id == pid), key=lambda f: f.day, reverse=True)
    for f in figs:
        if f.cost is not None and f.revenue:
            return float(f.cost) / float(f.revenue)
    months = sorted(await alive(db, ProductMonth, ProductMonth.product_id == pid), key=lambda m: m.month, reverse=True)
    for m in months:
        if m.revenue:
            return float(m.cost or 0) / float(m.revenue)
    return None


async def save_figure(db: AsyncSession, p: Product, f: ProductFigure, body: FigureIn) -> None:
    if p.tracking != "figures":
        raise HTTPException(status_code=400, detail="This product has a fixed monthly amount; it does not take figures.")
    if body.day > today().isoformat():
        raise HTTPException(status_code=400, detail="Pick a date up to today.")
    same = [x for x in await alive(db, ProductFigure, ProductFigure.product_id == p.id) if x.id != f.id and x.day[:7] == body.day[:7]]
    before = max((x for x in same if x.day < body.day), key=lambda x: x.day, default=None)
    after = min((x for x in same if x.day > body.day), key=lambda x: x.day, default=None)
    if before and body.revenue < float(before.revenue):
        raise HTTPException(status_code=400, detail=f"This is lower than the figure on {before.day} (₹{float(before.revenue):,.0f}). Revenue so far can only go up within a month.")
    if after and body.revenue > float(after.revenue):
        raise HTTPException(status_code=400, detail=f"This is higher than the later figure on {after.day} (₹{float(after.revenue):,.0f}).")
    cost = body.cost
    if cost is None and p.cost_label:
        ratio = await usual_cost_ratio(db, p.id)
        cost = round(body.revenue * ratio, 2) if ratio is not None else None
    f.day, f.revenue, f.cost, f.forecast = body.day, body.revenue, cost, body.forecast


@router.post("/products/{pid}/figures", response_model=APIResponse)
async def add_figure(pid: int, body: FigureIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    p = await get_or_404(db, Product, pid, "Product")
    f = ProductFigure(product_id=pid, day=body.day, revenue=0)
    await save_figure(db, p, f, body)
    db.add(f)
    await db.commit()
    return success_response(data=await everything(db) | {"id": f.id}, message="Figure added")


@router.put("/products/{pid}/figures/{fid}", response_model=APIResponse)
async def update_figure(pid: int, fid: int, body: FigureIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    p = await get_or_404(db, Product, pid, "Product")
    f = await get_or_404(db, ProductFigure, fid, "Figure")
    if f.product_id != pid:
        raise HTTPException(status_code=404, detail="Figure not found.")
    await save_figure(db, p, f, body)
    await db.commit()
    return success_response(data=await everything(db), message="Figure saved")


@router.delete("/products/{pid}/figures/{fid}", response_model=APIResponse)
async def delete_figure(pid: int, fid: int, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    f = await get_or_404(db, ProductFigure, fid, "Figure")
    if f.product_id != pid:
        raise HTTPException(status_code=404, detail="Figure not found.")
    f.is_deleted = True
    await db.commit()
    return success_response(data=await everything(db), message="Figure deleted")


@router.put("/products/{pid}/months/{month}", response_model=APIResponse)
async def set_product_month(pid: int, month: str, body: ProductMonthIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    check_month(month)
    p = await get_or_404(db, Product, pid, "Product")
    cost = body.cost
    if cost is None:
        ratio = await usual_cost_ratio(db, pid) if p.cost_label else None
        cost = round(body.revenue * ratio, 2) if ratio is not None else 0
    row = (await db.execute(select(ProductMonth).filter(ProductMonth.product_id == pid, ProductMonth.month == month))).scalars().first()
    if row:
        row.revenue, row.cost, row.is_deleted = body.revenue, cost, False
    else:
        db.add(ProductMonth(product_id=pid, month=month, revenue=body.revenue, cost=cost))
    await db.commit()
    return success_response(data=await everything(db), message="Month total saved")


@router.delete("/products/{pid}/months/{month}", response_model=APIResponse)
async def delete_product_month(pid: int, month: str, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    check_month(month)
    await db.execute(sa_delete(ProductMonth).where(ProductMonth.product_id == pid, ProductMonth.month == month))
    await db.commit()
    return success_response(data=await everything(db), message="Month total removed")


# ----------------------------------------------------------------- capital, bank, settings

@router.post("/capital", response_model=APIResponse)
async def add_capital(body: CapitalIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    c = CapitalEntry(day=body.day, amount=body.amount, note=(body.note or "").strip() or None)
    db.add(c)
    await db.commit()
    return success_response(data=await everything(db) | {"id": c.id}, message="Money put in added")


@router.put("/capital/{cid}", response_model=APIResponse)
async def update_capital(cid: int, body: CapitalIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    c = await get_or_404(db, CapitalEntry, cid, "Entry")
    c.day, c.amount, c.note = body.day, body.amount, (body.note or "").strip() or None
    await db.commit()
    return success_response(data=await everything(db), message="Saved")


@router.delete("/capital/{cid}", response_model=APIResponse)
async def delete_capital(cid: int, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    c = await get_or_404(db, CapitalEntry, cid, "Entry")
    c.is_deleted = True
    await db.commit()
    return success_response(data=await everything(db), message="Deleted")


@router.put("/bank", response_model=APIResponse)
async def set_bank(body: BankIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    if body.day > today().isoformat():
        raise HTTPException(status_code=400, detail="Pick a date up to today.")
    db.add(BankCheck(day=body.day, amount=body.amount))
    await db.commit()
    return success_response(data=await everything(db), message="Balance saved")


@router.put("/settings", response_model=APIResponse)
async def update_settings(body: SettingsIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    s = await get_settings(db)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    if s.amber_months > s.green_months:
        raise HTTPException(status_code=400, detail="The amber limit must be lower than the green one.")
    await db.commit()
    return success_response(data=await everything(db), message="Settings saved")


# ----------------------------------------------------------------- undo

RESTORABLE = {"expense": Expense, "product": Product, "figure": ProductFigure, "capital": CapitalEntry, "category": ExpenseCategory}


@router.post("/restore/{kind}/{rid}", response_model=APIResponse)
async def restore(kind: str, rid: int, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    model = RESTORABLE.get(kind)
    if not model:
        raise HTTPException(status_code=404, detail="Nothing to restore.")
    row = (await db.execute(select(model).filter(model.id == rid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Nothing to restore.")
    row.is_deleted = False
    await db.commit()
    return success_response(data=await everything(db), message="Restored")


# ----------------------------------------------------------------- bulk upload (CSV)

class ImportIn(BaseModel):
    csv: str = Field(..., max_length=ri.MAX_BYTES)
    replace: bool = True   # cells that already have a different figure take the file's figure


@router.get("/template/{kind}")
async def download_template(kind: str, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    """CSV template, pre-filled with what Runway already has (so re-uploading it changes nothing)."""
    data = await load(db)
    if kind == "monthly":
        text, name = ri.template_monthly(data, today()), "runway-monthly-history.csv"
    elif kind == "figures":
        text, name = ri.template_figures(data), "runway-product-figures.csv"
    else:
        raise HTTPException(status_code=404, detail="Unknown template.")
    # BOM so Excel opens ₹ and names correctly
    return Response(content="\ufeff" + text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/import/preview", response_model=APIResponse)
async def import_preview(body: ImportIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    plan = ri.parse(body.csv, await load(db), today())
    return success_response(data={k: plan[k] for k in ("kind", "errors", "warnings", "categories", "expenses", "products", "summary") if k in plan})


@router.post("/import/apply", response_model=APIResponse)
async def import_apply(body: ImportIn, db: AsyncSession = Depends(deps.get_db), user: User = Depends(ceo_only)) -> Any:
    data = await load(db)
    plan = ri.parse(body.csv, data, today())
    if plan["errors"]:
        raise HTTPException(status_code=400, detail=f"The file has {len(plan['errors'])} problem(s); fix them and upload again.")
    s = await get_settings(db)
    counts = {"added": 0, "changed": 0, "kept": 0}

    cat_ids = {c["name"].strip().lower(): c["id"] for c in data["categories"]}
    for name in plan["categories"]:
        c = ExpenseCategory(name=name, sort_order=len(cat_ids))
        db.add(c); await db.flush()
        cat_ids[name.lower()] = c.id
    exp_ids = {e["name"].strip().lower(): e["id"] for e in data["expenses"]}
    for e in plan["expenses"]:
        row = Expense(category_id=cat_ids[e["category"].lower()], name=e["name"], how=e["how"], amount=e["amount"],
                      start_month=e["start_month"] or s.start_month, end_month=e["end_month"], raise_pct=e["raise_pct"])
        db.add(row); await db.flush()
        exp_ids[e["name"].lower()] = row.id
    prod = {p["name"].strip().lower(): p["id"] for p in data["products"]}
    for p in plan["products"]:
        row = Product(name=p["name"], tracking="figures", cost_label=p["cost_label"], amount=0, start_month=p["start_month"],
                      sort_order=len(prod))
        db.add(row); await db.flush()
        prod[p["name"].lower()] = row.id
    for pname, label in (plan.get("product_labels") or {}).items():
        row = await get_or_404(db, Product, prod[pname.lower()], "Product")
        row.cost_label = label

    def decide(c) -> bool:
        if c["old"] is None:
            counts["added"] += 1; return True
        same = abs(float(c["old"]) - float(c["amount"])) < .005 and (c.get("cost") is None or c.get("old_cost") is None or abs(float(c["old_cost"]) - float(c["cost"])) < .005)
        if same or not body.replace:
            counts["kept"] += 1; return False
        counts["changed"] += 1; return True

    capital = await alive(db, CapitalEntry)
    figs = await alive(db, ProductFigure)
    for c in plan["cells"]:
        if not decide(c):
            continue
        t = c["target"]
        if t == "expense":
            await set_month(db, exp_ids[c["name"].lower()], c["month"], c["amount"])
        elif t == "product_month":
            pid = prod[c["name"].lower()]
            row = (await db.execute(select(ProductMonth).filter(ProductMonth.product_id == pid, ProductMonth.month == c["month"]))).scalars().first()
            cost = c.get("cost")
            if cost is None:
                cost = c.get("old_cost")
            if cost is None:
                ratio = await usual_cost_ratio(db, pid)
                cost = round(c["amount"] * ratio, 2) if ratio is not None else 0
            if row:
                row.revenue, row.cost, row.is_deleted = c["amount"], cost, False
            else:
                db.add(ProductMonth(product_id=pid, month=c["month"], revenue=c["amount"], cost=cost))
        elif t == "capital":
            row = next((x for x in capital if x.day[:7] == c["month"] and (x.note or "Capital").strip().lower() == c["name"].lower()), None)
            if row:
                row.amount = c["amount"]
            else:
                db.add(CapitalEntry(day=f"{c['month']}-01", amount=c["amount"], note=c["name"]))
        elif t == "figure":
            pid = prod[c["name"].lower()]
            row = next((f for f in figs if f.product_id == pid and f.day == c["day"]), None)
            if row:
                row.revenue, row.cost, row.forecast = c["amount"], c.get("cost") if c.get("cost") is not None else row.cost, c.get("forecast") if c.get("forecast") is not None else row.forecast
            else:
                db.add(ProductFigure(product_id=pid, day=c["day"], revenue=c["amount"], cost=c.get("cost"), forecast=c.get("forecast")))
        await db.flush()
    await db.commit()
    return success_response(data=await everything(db) | {"imported": counts | {
        "categories": len(plan["categories"]), "expenses": len(plan["expenses"]), "products": len(plan["products"])}}, message="Imported")

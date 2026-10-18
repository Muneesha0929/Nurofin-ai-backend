"""Runway: the CEO's own view of money in, money out and when the company breaks even.

Only the CEO can read or change any of this (see endpoints/runway.py). It is kept apart from
the older Finance module on purpose: those endpoints are readable by other roles.

Tables start with cos_fin_ ("Chief of Staff, finance") so they never collide with the
hand-made tables that already exist in production.

Months are stored as 'YYYY-MM' strings, days as 'YYYY-MM-DD' (same style as the rest of the app).
Money is Numeric(14, 2) in rupees, never Float.
"""
from sqlalchemy import Column, ForeignKey, Integer, Numeric, String, UniqueConstraint
from app.db.base_class import Base

MONEY = Numeric(14, 2)


class RunwaySettings(Base):
    """One row. The month the books start, the safety buffer and the on-track thresholds."""
    __tablename__ = "cos_fin_settings"

    id = Column(Integer, primary_key=True)
    start_month = Column(String(7), nullable=False, default="2025-12")
    buffer_pct = Column(Numeric(5, 2), nullable=False, default=5)
    green_months = Column(Integer, nullable=False, default=12)
    amber_months = Column(Integer, nullable=False, default=6)


class ExpenseCategory(Base):
    __tablename__ = "cos_fin_category"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)


class Expense(Base):
    """One cost line: a salary, AWS, rent, a one-off purchase.

    how = 'monthly' (same every month, raise_pct applied each April),
          'varies'  (every month, follows its own trend; amount is the usual figure),
          'once'    (only in the months it has an entry).
    """
    __tablename__ = "cos_fin_expense"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("cos_fin_category.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    how = Column(String(10), nullable=False, default="monthly")
    amount = Column(MONEY, nullable=False, default=0)
    start_month = Column(String(7), nullable=False)
    end_month = Column(String(7), nullable=True)
    raise_pct = Column(Numeric(5, 2), nullable=False, default=0)


class ExpenseMonth(Base):
    """The figure for one expense in one month.

    For a past month it is what was really paid. For the current or a future month it replaces
    the planned figure for that month only. For a one-time expense it is the payment itself.
    """
    __tablename__ = "cos_fin_expense_month"
    __table_args__ = (UniqueConstraint("expense_id", "month", name="uix_cos_fin_expense_month"),)

    id = Column(Integer, primary_key=True, index=True)
    expense_id = Column(Integer, ForeignKey("cos_fin_expense.id"), nullable=False, index=True)
    month = Column(String(7), nullable=False)
    amount = Column(MONEY, nullable=False)


class Product(Base):
    """Something the company earns from: GMDI today, others later.

    tracking = 'figures' (income changes month to month: enter "so far this month" figures
                          whenever you look, and month totals when a month closes),
               'fixed'   (the same amount every month: a retainer, a subscription).
    cost_label names the cost that grows with this product's revenue ("Bank charges" for GMDI);
    leave it empty when there is none.
    """
    __tablename__ = "cos_fin_product"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    tracking = Column(String(10), nullable=False, default="figures")
    cost_label = Column(String(80), nullable=True)
    amount = Column(MONEY, nullable=False, default=0)          # fixed products only
    start_month = Column(String(7), nullable=False)
    end_month = Column(String(7), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)


class ProductFigure(Base):
    """Revenue so far in a month, copied from the product's own dashboard on a given day."""
    __tablename__ = "cos_fin_product_figure"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("cos_fin_product.id"), nullable=False, index=True)
    day = Column(String(10), nullable=False)
    revenue = Column(MONEY, nullable=False)
    cost = Column(MONEY, nullable=True)          # e.g. bank cost so far
    forecast = Column(MONEY, nullable=True)      # the product's own month-end forecast, if it shows one


class ProductMonth(Base):
    """A product's final total for a month (revenue before GST, and its linked cost)."""
    __tablename__ = "cos_fin_product_month"
    __table_args__ = (UniqueConstraint("product_id", "month", name="uix_cos_fin_product_month"),)

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("cos_fin_product.id"), nullable=False, index=True)
    month = Column(String(7), nullable=False)
    revenue = Column(MONEY, nullable=False)
    cost = Column(MONEY, nullable=False, default=0)


class CapitalEntry(Base):
    """Money put into the company. A future date is a planned top-up."""
    __tablename__ = "cos_fin_capital"

    id = Column(Integer, primary_key=True, index=True)
    day = Column(String(10), nullable=False)
    amount = Column(MONEY, nullable=False)
    note = Column(String(200), nullable=True)


class BankCheck(Base):
    """What the bank statement said on a day; the latest one is compared with the books."""
    __tablename__ = "cos_fin_bank_check"

    id = Column(Integer, primary_key=True, index=True)
    day = Column(String(10), nullable=False)
    amount = Column(MONEY, nullable=False)


RUNWAY_TABLES = [
    "cos_fin_settings", "cos_fin_category", "cos_fin_expense", "cos_fin_expense_month",
    "cos_fin_product", "cos_fin_product_figure", "cos_fin_product_month",
    "cos_fin_capital", "cos_fin_bank_check",
]

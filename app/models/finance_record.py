from sqlalchemy import Column, Integer, String, Float, Enum, ForeignKey
from sqlalchemy.orm import relationship
import enum
from app.db.base_class import Base


class FinanceRecordTypeEnum(str, enum.Enum):
    budget = "budget"                # Project budget allocation
    expense = "expense"              # Operational / project expense
    salary = "salary"                # Employee salary / payroll
    vendor_payment = "vendor_payment"  # Payment due to a vendor
    renewal = "renewal"              # SaaS / subscription renewal
    revenue = "revenue"              # Income received
    other = "other"


class FinanceRecordStatusEnum(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    paid = "paid"
    overdue = "overdue"
    rejected = "rejected"


class CostCategoryEnum(str, enum.Enum):
    cloud = "cloud"                    # Cloud hosting (AWS, Azure, GCP)
    office = "office"                  # Office rent, supplies, utilities
    internet = "internet"              # Internet / telecom
    software = "software"              # Software licenses
    hardware = "hardware"              # Laptops, equipment
    marketing = "marketing"            # Ads, campaigns
    travel = "travel"                  # Business travel
    legal = "legal"                    # Legal / compliance
    insurance = "insurance"            # Insurance premiums
    contractor = "contractor"          # Contractor / freelancer fees
    other_expense = "other_expense"    # Miscellaneous expense


class FinanceRecord(Base):
    __tablename__ = "financerecord"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String)
    record_type = Column(Enum(FinanceRecordTypeEnum), default=FinanceRecordTypeEnum.expense)
    status = Column(Enum(FinanceRecordStatusEnum), default=FinanceRecordStatusEnum.pending)
    amount = Column(Float, default=0.0)
    currency = Column(String, default="USD")
    vendor = Column(String)
    department = Column(String)
    cost_category = Column(String, nullable=True)  # Sub-category for expense tracking

    project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True)  # For salary records

    due_date = Column(String, nullable=True)
    transaction_date = Column(String, nullable=True)
    notes = Column(String)

    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    project = relationship("Project")
    user = relationship("User", foreign_keys=[user_id])
    created_by = relationship("User", foreign_keys=[created_by_id])

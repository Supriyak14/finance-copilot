"""Database tables (Transaction, Category, Budget) as SQLAlchemy models."""

import enum
from datetime import date as date_type

from sqlalchemy import Date, Enum, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TransactionType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


# Reused by every Enum column below, so a bad string is rejected in one place.
def _tx_type_column(**kwargs):
    return mapped_column(
        Enum(
            TransactionType,
            name="transaction_type",
            values_callable=lambda e: [m.value for m in e],
            create_constraint=True,
            validate_strings=True,
        ),
        **kwargs,
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # create_constraint=True: SQLAlchemy omits the CHECK by default, which lets a
    # bad string be written and then breaks every later read of the table.
    # validate_strings=True rejects it at flush time too.
    type: Mapped[TransactionType] = _tx_type_column(nullable=False)
    # Numeric, not Float: money should not carry binary rounding error.
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)

    __table_args__ = (Index("ix_transactions_date_type", "date", "type"),)

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} {self.type.value} "
            f"{self.amount} {self.category!r} {self.date}>"
        )


class Category(Base):
    """Picklist for the add-transaction form. Seeded with sensible defaults."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[TransactionType] = _tx_type_column(nullable=False)

    # The same name can exist for both income and expense (e.g. "Other").
    __table_args__ = (UniqueConstraint("name", "type", name="uq_category_name_type"),)

    def __repr__(self) -> str:
        return f"<Category {self.name!r} ({self.type.value})>"


class Budget(Base):
    """A monthly spending cap for one expense category."""

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    monthly_limit: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    def __repr__(self) -> str:
        return f"<Budget {self.category!r} limit={self.monthly_limit}>"


DEFAULT_CATEGORIES: list[tuple[str, TransactionType]] = [
    ("Salary", TransactionType.INCOME),
    ("Freelance", TransactionType.INCOME),
    ("Investments", TransactionType.INCOME),
    ("Gifts", TransactionType.INCOME),
    ("Other Income", TransactionType.INCOME),
    ("Rent", TransactionType.EXPENSE),
    ("Groceries", TransactionType.EXPENSE),
    ("Dining Out", TransactionType.EXPENSE),
    ("Transport", TransactionType.EXPENSE),
    ("Utilities", TransactionType.EXPENSE),
    ("Shopping", TransactionType.EXPENSE),
    ("Entertainment", TransactionType.EXPENSE),
    ("Health", TransactionType.EXPENSE),
    ("Education", TransactionType.EXPENSE),
    ("Subscriptions", TransactionType.EXPENSE),
    ("Travel", TransactionType.EXPENSE),
    ("Other Expense", TransactionType.EXPENSE),
]

"""Calculations: totals, spend by category, monthly trends, budget status."""

from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Budget, Transaction, TransactionType
from app.schemas import BudgetStatus, CategoryTotal, MonthlyPoint, Summary

ZERO = Decimal("0.00")

# Percent of a budget that counts as "getting close" rather than comfortable.
WARNING_THRESHOLD = 80.0


def _money(value) -> Decimal:
    """SUM() returns None on an empty set and float for some drivers."""
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _date_filters(start: date_type | None, end: date_type | None) -> list:
    filters = []
    if start is not None:
        filters.append(Transaction.date >= start)
    if end is not None:
        filters.append(Transaction.date <= end)
    return filters


def month_bounds(day: date_type) -> tuple[date_type, date_type]:
    """First and last day of the month `day` falls in."""
    first = day.replace(day=1)
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)
    else:
        next_first = first.replace(month=first.month + 1)
    return first, next_first - timedelta(days=1)


def get_summary(
    db: Session, start: date_type | None = None, end: date_type | None = None
) -> Summary:
    """Income, expense, net and savings rate over an optional date window."""
    filters = _date_filters(start, end)

    rows = db.execute(
        select(
            Transaction.type,
            func.sum(Transaction.amount),
            func.count(Transaction.id),
        )
        .where(*filters)
        .group_by(Transaction.type)
    ).all()

    totals = {t: _money(s) for t, s, _ in rows}
    income = totals.get(TransactionType.INCOME, ZERO)
    expense = totals.get(TransactionType.EXPENSE, ZERO)
    count = sum(c for _, _, c in rows)

    net = income - expense
    # Savings rate is meaningless without income; report 0 rather than dividing by it.
    savings_rate = float(net / income * 100) if income > 0 else 0.0

    span = db.execute(
        select(func.min(Transaction.date), func.max(Transaction.date)).where(*filters)
    ).one()

    return Summary(
        total_income=income,
        total_expense=expense,
        net=net,
        savings_rate=round(savings_rate, 1),
        transaction_count=count,
        start_date=span[0],
        end_date=span[1],
    )


def get_spend_by_category(
    db: Session,
    start: date_type | None = None,
    end: date_type | None = None,
    tx_type: TransactionType = TransactionType.EXPENSE,
) -> list[CategoryTotal]:
    """Totals per category, biggest first — this is the pie chart."""
    rows = db.execute(
        select(Transaction.category, func.sum(Transaction.amount))
        .where(Transaction.type == tx_type, *_date_filters(start, end))
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
    ).all()

    totals = [(category, _money(total)) for category, total in rows]
    grand = sum((t for _, t in totals), ZERO)

    return [
        CategoryTotal(
            category=category,
            total=total,
            share=round(float(total / grand * 100), 1) if grand > 0 else 0.0,
        )
        for category, total in totals
    ]


def get_monthly_trend(db: Session, months: int = 6) -> list[MonthlyPoint]:
    """Income vs expense per month for the last `months` months that have data."""
    month_expr = func.strftime("%Y-%m", Transaction.date)

    rows = db.execute(
        select(month_expr, Transaction.type, func.sum(Transaction.amount))
        .group_by(month_expr, Transaction.type)
        .order_by(month_expr)
    ).all()

    by_month: dict[str, dict[TransactionType, Decimal]] = {}
    for month, tx_type, total in rows:
        by_month.setdefault(month, {})[tx_type] = _money(total)

    # Keep the most recent `months` buckets, then restore chronological order.
    recent = sorted(by_month)[-months:] if months > 0 else sorted(by_month)

    points = []
    for month in recent:
        income = by_month[month].get(TransactionType.INCOME, ZERO)
        expense = by_month[month].get(TransactionType.EXPENSE, ZERO)
        points.append(
            MonthlyPoint(month=month, income=income, expense=expense, net=income - expense)
        )
    return points


def get_budget_status(db: Session, today: date_type | None = None) -> list[BudgetStatus]:
    """Every budget measured against the current month's spending in that category."""
    today = today or date_type.today()
    first, last = month_bounds(today)

    spent_rows = db.execute(
        select(Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.date >= first,
            Transaction.date <= last,
        )
        .group_by(Transaction.category)
    ).all()
    spent_by_category = {category: _money(total) for category, total in spent_rows}

    budgets = db.execute(select(Budget).order_by(Budget.category)).scalars().all()

    statuses = []
    for budget in budgets:
        limit = _money(budget.monthly_limit)
        spent = spent_by_category.get(budget.category, ZERO)
        percent = round(float(spent / limit * 100), 1) if limit > 0 else 0.0

        if spent > limit:
            status = "over"
        elif percent >= WARNING_THRESHOLD:
            status = "warning"
        else:
            status = "ok"

        statuses.append(
            BudgetStatus(
                category=budget.category,
                monthly_limit=limit,
                spent=spent,
                # Clamp at zero: "remaining: -40" reads worse than over-budget alert.
                remaining=max(limit - spent, ZERO),
                percent_used=percent,
                status=status,
                month=first.strftime("%Y-%m"),
            )
        )
    return statuses


def build_financial_snapshot(db: Session, today: date_type | None = None) -> dict:
    """Everything the AI needs about this user's money, in one plain dict.

    Kept separate from the API responses so the prompt stays stable even if the
    HTTP schemas grow fields the model doesn't need.
    """
    today = today or date_type.today()
    first, _ = month_bounds(today)

    overall = get_summary(db)
    this_month = get_summary(db, start=first, end=today)

    return {
        "today": today.isoformat(),
        "overall": overall.model_dump(mode="json"),
        "this_month": this_month.model_dump(mode="json"),
        "top_expense_categories": [
            c.model_dump(mode="json") for c in get_spend_by_category(db)[:10]
        ],
        "monthly_trend": [p.model_dump(mode="json") for p in get_monthly_trend(db, 6)],
        "budgets": [b.model_dump(mode="json") for b in get_budget_status(db, today)],
    }

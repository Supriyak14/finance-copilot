"""Forecasting: where this month lands, and what the next few will cost.

Every number in here is computed in Python. Nothing is asked of a model, so the
forecast is deterministic, unit-testable, costs nothing and works with no API
key at all. The AI layer sits on top of this module and only explains numbers
that were already decided here — it never produces them.

The method, in one paragraph: take the last few *complete* months, split each
category into recurring bills (same amount, nearly every month) and variable
spending, forecast a bill as its median and variable spending as a
recency-weighted average nudged by a damped trend line, then project the month
in progress as "what you have already spent, plus the share of a normal month
still ahead".
"""

from datetime import date as date_type
from decimal import Decimal
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Budget, Transaction, TransactionType
from app.schemas import (
    BudgetRisk,
    CategoryForecast,
    CurrentMonthProjection,
    Forecast,
    MonthProjection,
    RecurringCharge,
)

# Imported rather than redefined: how money gets rounded should have exactly one
# definition in the codebase, or the two copies quietly drift apart.
from app.services.analytics_service import _money, month_bounds

ZERO = Decimal("0.00")
ONE = Decimal("1")

# Completed months to learn from. Six is enough to see a trend without letting
# habits from most of a year ago outvote how someone is spending now.
DEFAULT_LOOKBACK = 6
DEFAULT_HORIZON = 3

# A trend fitted to a handful of points is a weak signal. Projecting it forward
# at full strength turns a mild drift into an alarming cliff, so halve it.
TREND_DAMPING = 0.5

# What makes a category a recurring bill rather than ordinary spending: it lands
# in most months, at least three of them, for close to the same amount.
FIXED_MAX_VARIATION = 0.12
FIXED_MIN_MONTHS = 3
FIXED_MIN_COVERAGE = 0.75

# Month-to-month movement smaller than this is noise, not a trend worth naming.
TREND_BAND = 7.0

# Projected to land this close to the cap counts as at risk rather than fine.
AT_RISK_THRESHOLD = Decimal("0.9")

METHOD = "recency-weighted average with a damped trend, recurring bills held at their median"


# --- Small helpers ---------------------------------------------------------


def month_key(day: date_type) -> str:
    return day.strftime("%Y-%m")


def shift_month(key: str, months: int) -> str:
    """'2026-08' shifted by +2 is '2026-10'. Works across year boundaries."""
    year, month = (int(part) for part in key.split("-"))
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _weighted_average(values: list[Decimal]) -> Decimal:
    """Mean with recent months counting for more — weights 1, 2, 3 ... n."""
    if not values:
        return ZERO
    weights = range(1, len(values) + 1)
    total = sum(value * weight for value, weight in zip(values, weights))
    return _money(total / sum(weights))


def _slope(values: list[Decimal]) -> float:
    """Least-squares slope, in currency units per month."""
    count = len(values)
    if count < 2:
        return 0.0
    mean_x = (count - 1) / 2
    mean_y = float(sum(values)) / count
    denominator = sum((x - mean_x) ** 2 for x in range(count))
    if denominator == 0:
        return 0.0
    numerator = sum(
        (x - mean_x) * (float(value) - mean_y) for x, value in enumerate(values)
    )
    return numerator / denominator


def _variation(values: list[Decimal]) -> float:
    """Coefficient of variation — spread relative to size, so a 20 bill and a
    2000 one can be judged as steady or erratic on the same scale."""
    count = len(values)
    if count < 2:
        return 0.0
    mean = float(sum(values)) / count
    if mean == 0:
        return 0.0
    variance = sum((float(value) - mean) ** 2 for value in values) / count
    return variance**0.5 / mean


def _is_fixed(values: list[Decimal]) -> bool:
    """True when this looks like a bill rather than discretionary spending."""
    seen = [value for value in values if value > 0]
    if len(seen) < FIXED_MIN_MONTHS:
        return False
    if len(seen) < FIXED_MIN_COVERAGE * len(values):
        return False
    return _variation(seen) <= FIXED_MAX_VARIATION


def _project(values: list[Decimal], fixed: bool, steps: int = 1) -> Decimal:
    """Expected total `steps` whole months from the end of the window."""
    if not values:
        return ZERO
    if fixed:
        # A bill's median ignores the one month it was billed twice, which a
        # mean would happily bake into every future month.
        return _money(median([value for value in values if value > 0]))
    drift = Decimal(str(_slope(values) * TREND_DAMPING * steps))
    return max(_money(_weighted_average(values) + drift), ZERO)


def _confidence(values: list[Decimal], fixed: bool) -> str:
    if fixed:
        return "high"
    months, spread = len(values), _variation(values)
    if months >= 4 and spread < 0.30:
        return "high"
    if months >= 3 and spread < 0.60:
        return "medium"
    return "low"


def _project_month_end(
    actual: Decimal, expected: Decimal, fixed: bool, progress: float
) -> Decimal:
    """Where a category ends the month in progress.

    A bill lands once: either it already has, or it still will, and neither case
    depends on the date. Everything else is "what has been spent, plus the share
    of a normal month still ahead" — which early in the month leans almost
    entirely on history, and by the last day is simply what happened.
    """
    if fixed:
        return max(actual, expected)
    remaining = Decimal(str(max(0.0, 1.0 - progress)))
    return _money(actual + remaining * expected)


# --- Loading ---------------------------------------------------------------


def _monthly_series(db: Session) -> tuple[dict, set[str]]:
    """(category, type) -> {month: total}, plus every month that has any data."""
    month_expr = func.strftime("%Y-%m", Transaction.date)

    rows = db.execute(
        select(month_expr, Transaction.category, Transaction.type, func.sum(Transaction.amount))
        .group_by(month_expr, Transaction.category, Transaction.type)
    ).all()

    series: dict[tuple[str, TransactionType], dict[str, Decimal]] = {}
    months: set[str] = set()
    for month, category, tx_type, total in rows:
        series.setdefault((category, tx_type), {})[month] = _money(total)
        months.add(month)
    return series, months


def _typical_days(db: Session, months: list[str]) -> dict[tuple[str, TransactionType], int]:
    """The day of the month each category usually lands on."""
    if not months:
        return {}

    rows = db.execute(
        select(Transaction.category, Transaction.type, Transaction.date).where(
            func.strftime("%Y-%m", Transaction.date).in_(months)
        )
    ).all()

    days: dict[tuple[str, TransactionType], list[int]] = {}
    for category, tx_type, when in rows:
        days.setdefault((category, tx_type), []).append(when.day)
    return {key: int(median(values)) for key, values in days.items()}


def _current_month_actuals(
    db: Session, first: date_type, today: date_type
) -> dict[tuple[str, TransactionType], Decimal]:
    rows = db.execute(
        select(Transaction.category, Transaction.type, func.sum(Transaction.amount))
        .where(Transaction.date >= first, Transaction.date <= today)
        .group_by(Transaction.category, Transaction.type)
    ).all()
    return {(category, tx_type): _money(total) for category, tx_type, total in rows}


# --- The forecast ----------------------------------------------------------


def _describe_category(
    category: str, tx_type: TransactionType, values: list[Decimal]
) -> CategoryForecast:
    fixed = _is_fixed(values)
    average = _money(sum(values) / len(values))
    forecast = _project(values, fixed)
    change = forecast - average
    change_percent = round(float(change / average * 100), 1) if average > 0 else 0.0

    if change_percent > TREND_BAND:
        trend = "rising"
    elif change_percent < -TREND_BAND:
        trend = "falling"
    else:
        trend = "steady"

    return CategoryForecast(
        category=category,
        type=tx_type,
        kind="fixed" if fixed else "variable",
        average=average,
        forecast=forecast,
        change=change,
        change_percent=change_percent,
        trend=trend,
        confidence=_confidence(values, fixed),
        months_observed=sum(1 for value in values if value > 0),
    )


def build_forecast(
    db: Session,
    today: date_type | None = None,
    horizon: int = DEFAULT_HORIZON,
    lookback: int = DEFAULT_LOOKBACK,
) -> Forecast:
    """The whole forecast: this month's landing point, per-category expectations,
    the next few months, recurring bills, and which budgets are heading over."""
    today = today or date_type.today()
    first, last = month_bounds(today)
    current = month_key(today)

    series, all_months = _monthly_series(db)

    # Only complete months teach anything — the month in progress is a partial
    # total and would drag every average down.
    window = sorted(month for month in all_months if month < current)[-lookback:]

    windowed: dict[tuple[str, TransactionType], list[Decimal]] = {}
    for key, by_month in series.items():
        values = [by_month.get(month, ZERO) for month in window]
        # A category with nothing in the window has no signal to forecast from.
        if any(value > 0 for value in values):
            windowed[key] = values

    categories = [
        _describe_category(category, tx_type, values)
        for (category, tx_type), values in windowed.items()
    ]
    # Expenses first and biggest first: that is the order people scan for.
    categories.sort(key=lambda c: (c.type is not TransactionType.EXPENSE, -c.forecast))

    by_key = {(c.category, c.type): c for c in categories}

    # --- The month in progress
    days_in_month = last.day
    days_elapsed = today.day
    progress = days_elapsed / days_in_month

    actuals = _current_month_actuals(db, first, today)
    projected: dict[tuple[str, TransactionType], Decimal] = {}

    # Every category with either history or spending this month gets projected.
    for key in set(windowed) | set(actuals):
        category_forecast = by_key.get(key)
        projected[key] = _project_month_end(
            actual=actuals.get(key, ZERO),
            expected=category_forecast.forecast if category_forecast else ZERO,
            fixed=bool(category_forecast) and category_forecast.kind == "fixed",
            progress=progress,
        )

    def _total(source: dict, tx_type: TransactionType) -> Decimal:
        return _money(sum((v for (_, t), v in source.items() if t is tx_type), ZERO))

    projected_income = _total(projected, TransactionType.INCOME)
    projected_expense = _total(projected, TransactionType.EXPENSE)

    current_month = CurrentMonthProjection(
        month=current,
        days_elapsed=days_elapsed,
        days_in_month=days_in_month,
        progress=round(progress * 100, 1),
        income_so_far=_total(actuals, TransactionType.INCOME),
        expense_so_far=_total(actuals, TransactionType.EXPENSE),
        projected_income=projected_income,
        projected_expense=projected_expense,
        projected_net=projected_income - projected_expense,
    )

    # --- The months after this one
    horizon_points: list[MonthProjection] = []
    cumulative = ZERO
    for step in range(1, horizon + 1):
        income = expense = ZERO
        for (_, tx_type), values in windowed.items():
            amount = _project(values, _is_fixed(values), steps=step)
            if tx_type is TransactionType.INCOME:
                income += amount
            else:
                expense += amount
        net = income - expense
        cumulative += net
        horizon_points.append(
            MonthProjection(
                month=shift_month(current, step),
                projected_income=_money(income),
                projected_expense=_money(expense),
                projected_net=_money(net),
                cumulative_net=_money(cumulative),
            )
        )

    # --- Recurring bills, so the fixed floor of a month is visible on its own
    typical_days = _typical_days(db, window)
    recurring = [
        RecurringCharge(
            category=c.category,
            type=c.type,
            typical_amount=c.forecast,
            months_seen=c.months_observed,
            typical_day=typical_days.get((c.category, c.type), 1),
        )
        for c in categories
        if c.kind == "fixed"
    ]
    recurring.sort(key=lambda r: -r.typical_amount)

    # --- Budgets, judged against where the month is heading rather than today
    budget_risk = []
    for budget in db.execute(select(Budget).order_by(Budget.category)).scalars():
        key = (budget.category, TransactionType.EXPENSE)
        limit = _money(budget.monthly_limit)
        spend = projected.get(key, ZERO)

        if spend > limit:
            status = "will_exceed"
        elif spend >= limit * AT_RISK_THRESHOLD:
            status = "at_risk"
        else:
            status = "on_track"

        budget_risk.append(
            BudgetRisk(
                category=budget.category,
                monthly_limit=limit,
                spent_so_far=actuals.get(key, ZERO),
                projected_spend=spend,
                projected_overage=max(spend - limit, ZERO),
                status=status,
            )
        )
    # Trouble first: the budgets about to break are the reason to read this list.
    budget_risk.sort(key=lambda b: (-b.projected_overage, -b.projected_spend))

    # --- How much to trust any of it
    if len(window) >= 4:
        overall = "high"
    elif len(window) >= 2:
        overall = "medium"
    else:
        overall = "low"

    notes = []
    if not window:
        notes.append(
            "No complete month of history yet, so this is based only on what has "
            "been logged so far this month."
        )
    elif len(window) < 3:
        plural = "month" if len(window) == 1 else "months"
        notes.append(
            f"Only {len(window)} complete {plural} of history — these numbers will "
            f"settle down as more gets logged."
        )
    if any(c.confidence == "low" for c in categories):
        notes.append(
            "Categories marked low confidence swing a lot month to month; their "
            "forecasts are a rough middle, not a reliable figure."
        )

    return Forecast(
        generated_on=today,
        method=METHOD,
        months_of_history=len(window),
        history_from=window[0] if window else None,
        history_to=window[-1] if window else None,
        confidence=overall,
        current_month=current_month,
        categories=categories,
        horizon=horizon_points,
        recurring=recurring,
        budget_risk=budget_risk,
        notes=notes,
    )


def build_forecast_snapshot(db: Session, today: date_type | None = None) -> dict:
    """The forecast as a plain dict, for handing to the AI narrator."""
    return build_forecast(db, today=today).model_dump(mode="json")

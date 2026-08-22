"""Fill the database with a realistic spending history.

Charts, budgets and forecasting all need months of data before they show
anything interesting, and typing that by hand is nobody's idea of a good time.

    python scripts/seed_demo.py           # add demo data
    python scripts/seed_demo.py --reset   # wipe transactions/budgets first

The random draws are seeded, so re-running gives the same history and any
forecast numbers you see in a demo stay put.
"""

import argparse
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Budget, Transaction, TransactionType  # noqa: E402

MONTHS_OF_HISTORY = 7
SEED = 20260822

# Monthly caps to seed, chosen so a couple of them are genuinely under pressure —
# a budget nobody ever hits demonstrates nothing.
BUDGETS = {
    "Groceries": "520.00",
    "Dining Out": "260.00",
    "Shopping": "300.00",
    "Transport": "220.00",
    "Entertainment": "150.00",
}


def month_starts(today: date, count: int) -> list[date]:
    """First day of each of the last `count` months, oldest first."""
    starts = []
    cursor = today.replace(day=1)
    for _ in range(count):
        starts.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return list(reversed(starts))


def days_in_month(first: date) -> int:
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    return (nxt - first).days


def money(rng: random.Random, low: float, high: float) -> Decimal:
    return Decimal(f"{rng.uniform(low, high):.2f}")


def tx(type_: TransactionType, amount, category: str, description: str, when: date):
    return Transaction(
        type=type_,
        amount=Decimal(str(amount)),
        category=category,
        description=description,
        date=when,
    )


def build_history(today: date, rng: random.Random) -> list[Transaction]:
    """One month at a time, oldest first. `today` caps the final partial month."""
    rows: list[Transaction] = []

    for index, first in enumerate(month_starts(today, MONTHS_OF_HISTORY)):
        last_day = min(days_in_month(first), today.day if first.month == today.month
                       and first.year == today.year else days_in_month(first))

        def on(day: int) -> date | None:
            """A date in this month, or None if the month hasn't reached it yet."""
            day = min(day, days_in_month(first))
            return first.replace(day=day) if day <= last_day else None

        def scatter(count: int) -> list[date]:
            """`count` random days spread through the elapsed part of the month."""
            return [first.replace(day=rng.randint(1, last_day)) for _ in range(count)]

        # --- Income: a steady salary, with freelance work turning up now and then.
        if (payday := on(1)) is not None:
            rows.append(tx(TransactionType.INCOME, "5200.00", "Salary", "Monthly salary", payday))
        if rng.random() < 0.45 and (gig := on(rng.randint(8, 24))) is not None:
            rows.append(
                tx(TransactionType.INCOME, money(rng, 300, 1400), "Freelance",
                   "Freelance project", gig)
            )

        # --- Fixed costs: same amount, same day, every month. The forecaster
        # should recognise these and stop treating them as noise.
        if (rent_day := on(3)) is not None:
            rows.append(tx(TransactionType.EXPENSE, "1450.00", "Rent", "Monthly rent", rent_day))
        if (subs_day := on(12)) is not None:
            rows.append(
                tx(TransactionType.EXPENSE, "42.97", "Subscriptions",
                   "Streaming + cloud storage", subs_day)
            )

        # --- Utilities: same bill each month, but the amount moves with the weather.
        if (bill_day := on(18)) is not None:
            rows.append(
                tx(TransactionType.EXPENSE, money(rng, 96, 178), "Utilities",
                   "Electricity and water", bill_day)
            )

        # --- Groceries: a weekly-ish shop.
        for day in scatter(rng.randint(4, 6)):
            rows.append(
                tx(TransactionType.EXPENSE, money(rng, 38, 132), "Groceries",
                   rng.choice(["Weekly shop", "Supermarket", "Corner store top-up"]), day)
            )

        # --- Dining out: deliberately drifting upward month over month, so the
        # trend line and the "rising" flag have something real to detect.
        creep = 1.0 + 0.055 * index
        for day in scatter(rng.randint(5, 9)):
            rows.append(
                tx(TransactionType.EXPENSE, money(rng, 11 * creep, 62 * creep), "Dining Out",
                   rng.choice(["Lunch out", "Dinner with friends", "Coffee", "Takeaway"]), day)
            )

        for day in scatter(rng.randint(7, 14)):
            rows.append(
                tx(TransactionType.EXPENSE, money(rng, 2.5, 38), "Transport",
                   rng.choice(["Bus fare", "Taxi", "Fuel", "Train ticket"]), day)
            )

        for day in scatter(rng.randint(1, 3)):
            rows.append(
                tx(TransactionType.EXPENSE, money(rng, 22, 210), "Shopping",
                   rng.choice(["Clothes", "Household goods", "Electronics"]), day)
            )

        for day in scatter(rng.randint(1, 4)):
            rows.append(
                tx(TransactionType.EXPENSE, money(rng, 9, 58), "Entertainment",
                   rng.choice(["Cinema", "Concert ticket", "Games"]), day)
            )

        # --- Occasional: these should come out as low-confidence forecasts.
        if rng.random() < 0.4 and (health := on(rng.randint(2, 26))) is not None:
            rows.append(
                tx(TransactionType.EXPENSE, money(rng, 18, 190), "Health",
                   rng.choice(["Pharmacy", "Dentist", "Check-up"]), health)
            )
        if rng.random() < 0.18 and (trip := on(rng.randint(4, 22))) is not None:
            rows.append(
                tx(TransactionType.EXPENSE, money(rng, 240, 900), "Travel",
                   "Trip", trip)
            )

    rows.sort(key=lambda r: r.date)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo transactions and budgets.")
    parser.add_argument(
        "--reset", action="store_true",
        help="delete existing transactions and budgets before seeding",
    )
    args = parser.parse_args()

    init_db()  # tables + the category picklist, in case this runs before the server
    rng = random.Random(SEED)
    today = date.today()

    with SessionLocal() as db:
        if args.reset:
            removed = db.query(Transaction).delete()
            db.query(Budget).delete()
            db.commit()
            print(f"Cleared {removed} existing transactions and all budgets.")
        elif db.query(Transaction).count():
            print(
                "The database already has transactions. Re-run with --reset to "
                "replace them, or leave it as it is."
            )
            return

        rows = build_history(today, rng)
        db.add_all(rows)

        existing = {b.category for b in db.query(Budget)}
        db.add_all(
            Budget(category=category, monthly_limit=Decimal(limit))
            for category, limit in BUDGETS.items()
            if category not in existing
        )
        db.commit()

        span = f"{rows[0].date} to {rows[-1].date}"
        income = sum(r.amount for r in rows if r.type is TransactionType.INCOME)
        expense = sum(r.amount for r in rows if r.type is TransactionType.EXPENSE)

    print(f"Added {len(rows)} transactions spanning {span}.")
    print(f"  income  {income:>10,.2f}")
    print(f"  expense {expense:>10,.2f}")
    print(f"  net     {income - expense:>10,.2f}")
    print(f"Set {len(BUDGETS)} monthly budgets: {', '.join(BUDGETS)}.")


if __name__ == "__main__":
    main()

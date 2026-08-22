"""Forecasting tests.

The forecast is plain arithmetic, so it can be pinned down exactly: every test
below builds a known history, freezes "today", and checks the number that comes
out. No API key and no network are involved anywhere in this file.

    PYTHONPATH=. venv/bin/python tests/test_forecast.py
"""

import os
import pathlib
import re
import tempfile
from datetime import date
from decimal import Decimal

os.environ["DATABASE_URL"] = f"sqlite:///{pathlib.Path(tempfile.mkdtemp()) / 'forecast_test.db'}"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Budget, Transaction, TransactionType  # noqa: E402
from app.services import forecast_service as fs  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(label, condition, detail=""):
    results.append(bool(condition))
    print(f"  [{PASS if condition else FAIL}] {label}" + (f" — {detail}" if detail else ""))


def section(title):
    print(f"\n=== {title} ===")


# --- A history we can predict by hand --------------------------------------
#
# Feb–Jul 2026 are complete months; August is in progress. Rent never moves,
# Coffee climbs by exactly 20 a month, and Dentist happens once.

TODAY = date(2026, 8, 16)
MONTHS = [(2026, m) for m in range(2, 8)]  # Feb .. Jul


def seed():
    init_db()
    with SessionLocal() as db:
        db.query(Transaction).delete()
        db.query(Budget).delete()

        rows = []
        for index, (year, month) in enumerate(MONTHS):
            rows.append(Transaction(type=TransactionType.EXPENSE, amount=Decimal("1000.00"),
                                    category="Rent", date=date(year, month, 3)))
            rows.append(Transaction(type=TransactionType.EXPENSE,
                                    amount=Decimal(100 + 20 * index),
                                    category="Coffee", date=date(year, month, 10)))
            rows.append(Transaction(type=TransactionType.INCOME, amount=Decimal("3000.00"),
                                    category="Salary", date=date(year, month, 1)))
        # One-off, so it should come out as a low-confidence forecast.
        rows.append(Transaction(type=TransactionType.EXPENSE, amount=Decimal("400.00"),
                                category="Dentist", date=date(2026, 5, 9)))

        # The month in progress: rent already paid, a little coffee so far.
        rows.append(Transaction(type=TransactionType.EXPENSE, amount=Decimal("1000.00"),
                                category="Rent", date=date(2026, 8, 3)))
        rows.append(Transaction(type=TransactionType.EXPENSE, amount=Decimal("50.00"),
                                category="Coffee", date=date(2026, 8, 5)))

        db.add_all(rows)
        db.add(Budget(category="Coffee", monthly_limit=Decimal("120.00")))
        db.add(Budget(category="Rent", monthly_limit=Decimal("2000.00")))
        db.commit()


seed()
client = TestClient(app)

with SessionLocal() as db:
    forecast = fs.build_forecast(db, today=TODAY)
by_category = {c.category: c for c in forecast.categories}


# --- 1. The arithmetic -----------------------------------------------------

section("1. THE ARITHMETIC")

values = [Decimal(v) for v in ("100", "120", "140", "160", "180", "200")]

# 100*1 + 120*2 + ... + 200*6 = 3500, over weights summing to 21.
check("weighted average leans on recent months",
      fs._weighted_average(values) == Decimal("166.67"), str(fs._weighted_average(values)))
check("plain mean would have been lower", Decimal("166.67") > sum(values) / len(values))
check("slope of a +20/month series is 20", fs._slope(values) == 20.0, str(fs._slope(values)))
check("slope of a flat series is 0", fs._slope([Decimal("5")] * 4) == 0.0)
check("slope needs two points", fs._slope([Decimal("5")]) == 0.0)
check("variation of an identical series is 0", fs._variation([Decimal("7")] * 5) == 0.0)
check("variation survives an all-zero series", fs._variation([Decimal("0")] * 5) == 0.0)

check("a steady monthly charge reads as fixed", fs._is_fixed([Decimal("42.97")] * 6))
check("a swinging charge does not", fs._is_fixed(values) is False)
check("two appearances are not enough to call it a bill",
      fs._is_fixed([Decimal("50"), Decimal("50"), Decimal("0"), Decimal("0")]) is False)
check("a bill missed in most months is not fixed",
      fs._is_fixed([Decimal("50")] * 3 + [Decimal("0")] * 5) is False)

check("a fixed charge forecasts at its median",
      fs._project([Decimal("100")] * 5, fixed=True) == Decimal("100.00"))
check("a trend is damped, not followed at full strength",
      fs._project(values, fixed=False) == Decimal("176.67"),
      f"166.67 + half of 20 = {fs._project(values, fixed=False)}")
check("projecting further out extends the trend",
      fs._project(values, fixed=False, steps=3) == Decimal("196.67"))
check("a forecast never goes negative",
      fs._project([Decimal("300"), Decimal("200"), Decimal("100"), Decimal("0")],
                  fixed=False, steps=12) == Decimal("0.00"))
check("no history forecasts nothing", fs._project([], fixed=False) == Decimal("0.00"))

check("shift_month walks forward", fs.shift_month("2026-08", 3) == "2026-11")
check("shift_month crosses the year", fs.shift_month("2026-11", 3) == "2027-02")
check("shift_month walks backward", fs.shift_month("2026-01", -1) == "2025-12")


# --- 2. Categories ---------------------------------------------------------

section("2. CATEGORIES")

check("rent is recognised as a bill", by_category["Rent"].kind == "fixed")
check("rent forecasts at exactly its usual amount",
      by_category["Rent"].forecast == Decimal("1000.00"))
check("a bill is high confidence", by_category["Rent"].confidence == "high")
check("a bill is not labelled a trend", by_category["Rent"].trend == "steady")

check("coffee is variable, not a bill", by_category["Coffee"].kind == "variable")
check("coffee's climb is picked up", by_category["Coffee"].trend == "rising",
      f"{by_category['Coffee'].change_percent}%")
check("coffee forecasts above its own average",
      by_category["Coffee"].forecast > by_category["Coffee"].average,
      f"{by_category['Coffee'].forecast} vs {by_category['Coffee'].average}")
check("coffee's average is the plain mean", by_category["Coffee"].average == Decimal("150.00"))

check("a one-off is low confidence", by_category["Dentist"].confidence == "low")
check("a one-off is still forecast, spread thin",
      Decimal("0") < by_category["Dentist"].forecast < Decimal("400.00"),
      str(by_category["Dentist"].forecast))
check("months_observed counts only months with spend",
      by_category["Dentist"].months_observed == 1)

check("income is forecast too", by_category["Salary"].type is TransactionType.INCOME)
check("expenses are listed before income",
      forecast.categories[0].type is TransactionType.EXPENSE)
check("the biggest expense leads the list", forecast.categories[0].category == "Rent")


# --- 3. The month in progress ---------------------------------------------

section("3. THE MONTH IN PROGRESS")

month = forecast.current_month
check("history stops at the last complete month", forecast.history_to == "2026-07",
      forecast.history_to)
check("the partial month is not learned from", forecast.months_of_history == 6)
check("day of month is reported", (month.days_elapsed, month.days_in_month) == (16, 31))
check("progress is a percentage", month.progress == 51.6, str(month.progress))

check("an already-paid bill is not counted twice",
      month.expense_so_far >= Decimal("1050.00") and
      month.projected_expense < month.expense_so_far + Decimal("1000.00"),
      f"so far {month.expense_so_far}, projected {month.projected_expense}")
check("a projection is never below what is already spent",
      month.projected_expense >= month.expense_so_far)
check("spending still to come is added on",
      month.projected_expense > month.expense_so_far)
check("net is income minus expense",
      month.projected_net == month.projected_income - month.projected_expense)

check("a paid bill projects to exactly itself",
      fs._project_month_end(Decimal("1000"), Decimal("1000"), fixed=True, progress=0.1)
      == Decimal("1000"))
check("an unpaid bill still projects in full",
      fs._project_month_end(Decimal("0"), Decimal("1000"), fixed=True, progress=0.9)
      == Decimal("1000"))
check("on day one a variable category leans on history",
      fs._project_month_end(Decimal("0"), Decimal("300"), fixed=False, progress=0.0)
      == Decimal("300.00"))
check("on the last day it is simply what happened",
      fs._project_month_end(Decimal("180"), Decimal("300"), fixed=False, progress=1.0)
      == Decimal("180.00"))
check("halfway through, half a normal month is still ahead",
      fs._project_month_end(Decimal("100"), Decimal("300"), fixed=False, progress=0.5)
      == Decimal("250.00"))


# --- 4. Months ahead -------------------------------------------------------

section("4. MONTHS AHEAD")

check("the default horizon is three months", len(forecast.horizon) == 3)
check("the horizon starts after the current month",
      forecast.horizon[0].month == "2026-09", forecast.horizon[0].month)
check("the horizon runs consecutively",
      [h.month for h in forecast.horizon] == ["2026-09", "2026-10", "2026-11"])
check("cumulative net accumulates",
      forecast.horizon[2].cumulative_net ==
      sum(h.projected_net for h in forecast.horizon))
check("each month's net is its own income minus expense",
      all(h.projected_net == h.projected_income - h.projected_expense
          for h in forecast.horizon))
with SessionLocal() as db:
    check("a longer horizon returns more months",
          len(fs.build_forecast(db, today=TODAY, horizon=6).horizon) == 6)


# --- 5. Recurring bills ----------------------------------------------------

section("5. RECURRING BILLS")

recurring = {r.category: r for r in forecast.recurring}
check("rent is listed as recurring", "Rent" in recurring)
check("salary is listed as recurring", "Salary" in recurring)
check("coffee is not", "Coffee" not in recurring)
check("the day it lands is recorded", recurring["Rent"].typical_day == 3,
      str(recurring["Rent"].typical_day))
check("the amount matches the forecast", recurring["Rent"].typical_amount == Decimal("1000.00"))


# --- 6. Budgets, judged on where the month is heading ----------------------

section("6. BUDGET RISK")

risk = {b.category: b for b in forecast.budget_risk}
coffee = risk["Coffee"]
check("a budget is measured against the projection, not today's total",
      coffee.spent_so_far == Decimal("50.00") and coffee.projected_spend > Decimal("120.00"),
      f"spent {coffee.spent_so_far}, projected {coffee.projected_spend}")
check("today's spend alone would have looked fine",
      coffee.spent_so_far < coffee.monthly_limit)
check("the forecast flags it anyway", coffee.status == "will_exceed", coffee.status)
check("the overage is quantified",
      coffee.projected_overage == coffee.projected_spend - coffee.monthly_limit)
check("a budget with room to spare reads on_track",
      risk["Rent"].status == "on_track", risk["Rent"].status)
check("trouble is sorted to the top",
      forecast.budget_risk[0].category == "Coffee")

# The same projection against a slightly roomier cap should land in the warning
# band rather than over it — the two thresholds have to be distinguishable.
with SessionLocal() as db:
    db.query(Budget).filter(Budget.category == "Coffee").update(
        {"monthly_limit": Decimal("140.00")})
    db.commit()
    warned = {b.category: b for b in
              fs.build_forecast(db, today=TODAY).budget_risk}["Coffee"]
    db.query(Budget).filter(Budget.category == "Coffee").update(
        {"monthly_limit": Decimal("120.00")})
    db.commit()

check("a budget it lands just under reads at_risk", warned.status == "at_risk",
      f"{warned.projected_spend} against {warned.monthly_limit} — {warned.status}")
check("at_risk carries no overage", warned.projected_overage == Decimal("0.00"))


# --- 7. Thin and empty histories ------------------------------------------

section("7. THIN AND EMPTY HISTORIES")

with SessionLocal() as db:
    early = fs.build_forecast(db, today=date(2026, 3, 10))
check("one complete month still produces a forecast", early.months_of_history == 1)
check("and says so in the notes", any("complete month" in n for n in early.notes),
      "; ".join(early.notes)[:70])
check("one month is low confidence", early.confidence == "low", early.confidence)

with SessionLocal() as db:
    db.query(Transaction).delete()
    db.query(Budget).delete()
    db.commit()
    empty = fs.build_forecast(db, today=TODAY)

check("an empty database does not crash", empty.months_of_history == 0)
check("nothing is claimed about categories", empty.categories == [])
check("the projection is zero, not None", empty.current_month.projected_expense == Decimal("0.00"))
check("confidence is low", empty.confidence == "low")
check("a note explains why", any("No complete month" in n for n in empty.notes),
      "; ".join(empty.notes)[:70])

seed()  # put the history back for the endpoint tests


# --- 8. The endpoint -------------------------------------------------------

section("8. THE ENDPOINT")

response = client.get("/api/forecast")
check("GET /api/forecast returns 200", response.status_code == 200, str(response.status_code))
body = response.json()
check("every section is present",
      all(key in body for key in
          ("current_month", "categories", "horizon", "recurring", "budget_risk",
           "confidence", "notes", "method")))
check("the method is stated for the reader", "trend" in body["method"], body["method"])

check("horizon is honoured", len(client.get("/api/forecast?horizon=6").json()["horizon"]) == 6)
check("lookback is honoured",
      client.get("/api/forecast?lookback=2").json()["months_of_history"] == 2)
check("horizon 0 is rejected", client.get("/api/forecast?horizon=0").status_code == 422)
check("an absurd horizon is rejected", client.get("/api/forecast?horizon=99").status_code == 422)
check("lookback 0 is rejected", client.get("/api/forecast?lookback=0").status_code == 422)

# Money must survive the trip as exact 2dp strings, never as floats.
decimal_like = re.compile(r"^-?\d+\.\d+$")


def money_strings(node, found=None):
    found = [] if found is None else found
    if isinstance(node, dict):
        for value in node.values():
            money_strings(value, found)
    elif isinstance(node, list):
        for value in node:
            money_strings(value, found)
    elif isinstance(node, str) and decimal_like.match(node):
        found.append(node)
    return found


amounts = money_strings(body)
check("amounts travel as strings, not floats", len(amounts) > 20, f"{len(amounts)} found")
check("every amount has exactly two decimal places",
      all(len(a.split(".")[1]) == 2 for a in amounts),
      next((a for a in amounts if len(a.split(".")[1]) != 2), ""))
check("no amount arrived as a float",
      not any(isinstance(v, float) for v in
              [c["forecast"] for c in body["categories"]]))


# --- 9. Determinism --------------------------------------------------------

section("9. DETERMINISM")

check("the same history gives the same answer twice",
      client.get("/api/forecast").json() == body)
with SessionLocal() as db:
    a = fs.build_forecast(db, today=TODAY)
    b = fs.build_forecast(db, today=TODAY)
check("and the service agrees with itself", a == b)


# --- 10. The AI narration sits on top, never underneath --------------------

section("10. AI NARRATION")

from app.config import settings  # noqa: E402

_key, settings.anthropic_api_key = settings.anthropic_api_key, ""
_mode, settings.ai_demo_mode = settings.ai_demo_mode, "off"
narrated = client.post("/api/ai/forecast")

check("narration with no key and no fallback returns 503", narrated.status_code == 503,
      str(narrated.status_code))
check("and says what to do about it", "ANTHROPIC_API_KEY" in narrated.json()["detail"])

settings.ai_demo_mode = "auto"
fallback = client.post("/api/ai/forecast")
settings.anthropic_api_key, settings.ai_demo_mode = _key, _mode

check("with the fallback on it answers anyway", fallback.status_code == 200,
      str(fallback.status_code))
check("and is labelled as a stand-in", fallback.json()["model"] == "demo")
check("the stand-in narrates the same computed numbers",
      "days into a" in fallback.json()["narrative"], fallback.json()["narrative"][:50])
check("the numbers themselves never needed a key",
      client.get("/api/forecast").status_code == 200)

with SessionLocal() as db:
    snapshot = fs.build_forecast_snapshot(db)
check("the AI is handed a finished forecast, not raw transactions",
      "categories" in snapshot and "budget_risk" in snapshot)
check("the snapshot is plain JSON types",
      all(not isinstance(v, Decimal) for v in snapshot["current_month"].values()))


# --- Result ----------------------------------------------------------------

print("\n" + "=" * 46)
passed = sum(results)
print(f"{passed}/{len(results)} checks passed")
raise SystemExit(0 if passed == len(results) else 1)

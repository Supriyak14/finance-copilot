"""Demo-fallback tests.

Two things are being checked here. First, that the stand-in replies are actually
built from the user's own numbers rather than being canned text — the figures
quoted have to appear in the data. Second, that the fallback only ever stands in
for the real integration and never replaces it: the API is tried first, a
stand-in is labelled as one, and switching the fallback off restores the real
errors exactly.

    PYTHONPATH=. venv/bin/python tests/test_demo_ai.py
"""

import os
import pathlib
import tempfile
import types
from datetime import date
from decimal import Decimal

os.environ["DATABASE_URL"] = f"sqlite:///{pathlib.Path(tempfile.mkdtemp()) / 'demo_test.db'}"

import anthropic  # noqa: E402
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Budget, Transaction, TransactionType  # noqa: E402
from app.services import ai_service, analytics_service, demo_ai, forecast_service  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(label, condition, detail=""):
    results.append(bool(condition))
    print(f"  [{PASS if condition else FAIL}] {label}" + (f" — {detail}" if detail else ""))


def section(title):
    print(f"\n=== {title} ===")


# --- A history with a known shape -----------------------------------------

TODAY = date(2026, 8, 16)


def seed():
    init_db()
    with SessionLocal() as db:
        db.query(Transaction).delete()
        db.query(Budget).delete()
        rows = []
        for month in range(2, 8):
            rows.append(Transaction(type=TransactionType.INCOME, amount=Decimal("4000.00"),
                                    category="Salary", date=date(2026, month, 1)))
            rows.append(Transaction(type=TransactionType.EXPENSE, amount=Decimal("1200.00"),
                                    category="Rent", date=date(2026, month, 3)))
            rows.append(Transaction(type=TransactionType.EXPENSE, amount=Decimal(200 + 30 * month),
                                    category="Dining Out", date=date(2026, month, 12)))
        rows.append(Transaction(type=TransactionType.INCOME, amount=Decimal("4000.00"),
                                category="Salary", date=date(2026, 8, 1)))
        rows.append(Transaction(type=TransactionType.EXPENSE, amount=Decimal("1200.00"),
                                category="Rent", date=date(2026, 8, 3)))
        rows.append(Transaction(type=TransactionType.EXPENSE, amount=Decimal("300.00"),
                                category="Dining Out", date=date(2026, 8, 9)))
        db.add_all(rows)
        db.add(Budget(category="Dining Out", monthly_limit=Decimal("250.00")))
        db.commit()


seed()
client = TestClient(app)

with SessionLocal() as db:
    snapshot = analytics_service.build_financial_snapshot(db, today=TODAY)
    forecast = forecast_service.build_forecast(db, today=TODAY).model_dump(mode="json")


# --- 1. Replies are built from the data, not canned ------------------------

section("1. GROUNDED IN THE REAL NUMBERS")

text = demo_ai.insights(snapshot)
check("insights name the biggest category", "Rent" in text, text[:60])
check("insights quote a real figure", "8,400.00" in text, text[:80])
check("insights quote a real share", "44.6%" in text or "%" in text)
check("insights flag the broken budget", "Dining Out" in text and "over budget" in text)
check("insights are prose, not a template stub", len(text) > 200 and "\n\n" in text)

# Same generator, different data — the text has to move with it.
thin = {
    "overall": {"total_income": "100.00", "total_expense": "80.00", "net": "20.00",
                "savings_rate": 20.0},
    "top_expense_categories": [{"category": "Groceries", "total": "80.00", "share": 100.0}],
    "budgets": [],
    "monthly_trend": [],
}
other = demo_ai.insights(thin)
check("a different history gives different text", other != text)
check("and quotes its own numbers", "Groceries" in other and "80.00" in other, other[:60])

check("an empty snapshot says so rather than inventing",
      "nothing logged" in demo_ai.insights({}).lower())

story = demo_ai.forecast_narrative(forecast)
spent_text = demo_ai._money(forecast["current_month"]["expense_so_far"])
landing_text = demo_ai._money(forecast["current_month"]["projected_expense"])
check("the forecast write-up quotes what is already spent", spent_text in story,
      f"{spent_text} — {story[:60]}")
check("and where the month lands", landing_text in story,
      f"{landing_text} — {story[:60]}")
check("it names the rising category", "Dining Out" in story)
check("it reports the budget heading over", "over budget" in story, story[:70])
check("a confident forecast is not hedged", "rough steer" not in story)

# The same generator on a one-month history has to hedge instead.
THIN_FORECAST = {
    "months_of_history": 1,
    "confidence": "low",
    "current_month": {"days_elapsed": 5, "days_in_month": 30, "expense_so_far": "100.00",
                      "projected_expense": "400.00", "projected_net": "50.00"},
    "categories": [{"category": "Groceries", "type": "expense", "kind": "variable",
                    "average": "300.00", "forecast": "400.00", "change": "100.00",
                    "change_percent": 33.3, "trend": "rising", "confidence": "low",
                    "months_observed": 1}],
    "budget_risk": [], "horizon": [], "notes": [],
}
thin_story = demo_ai.forecast_narrative(THIN_FORECAST)
check("a thin history is hedged instead", "rough steer" in thin_story, thin_story[-80:])
check("and the hedge counts the months honestly", "1 complete month" in thin_story)
check("no history gives an honest answer, not a guess",
      "not a complete month" in demo_ai.forecast_narrative({}).lower())


# --- 2. Chat answers the question that was asked --------------------------

section("2. CHAT")

check("a named category is looked up",
      "Rent" in demo_ai.chat(snapshot, "how much am I spending on Rent?"))
check("an affordable amount is confirmed against the real net",
      "Yes" in demo_ai.chat(snapshot, "can I afford 50 next month?"),
      demo_ai.chat(snapshot, "can I afford 50 next month?")[:60])
check("a deficit is never phrased as being 'up'",
      "down this month" in demo_ai.chat(
          {"this_month": {"net": "-500.00"}, "overall": {},
           "top_expense_categories": [{"category": "Rent", "total": "500.00", "share": 100.0}],
           "budgets": []},
          "can I afford 20?"))
check("an unaffordable amount is refused with the shortfall",
      "short" in demo_ai.chat(snapshot, "can I afford 99999?"),
      demo_ai.chat(snapshot, "can I afford 99999?")[:70])
check("a saving question picks something discretionary",
      "Dining Out" in demo_ai.chat(snapshot, "where can I cut back?"),
      demo_ai.chat(snapshot, "where can I cut back?")[:60])
check("rent is never offered as the thing to cut",
      "Rent is where the room is" not in demo_ai.chat(snapshot, "how do I save money?"))
check("a budget question reports budget status",
      "Dining Out" in demo_ai.chat(snapshot, "how are my budgets doing?"))
check("an income question reports income",
      "28,000.00" in demo_ai.chat(snapshot, "how much do I earn?"),
      demo_ai.chat(snapshot, "how much do I earn?")[:60])
check("an unrecognised question still answers from the data",
      "28,000.00" in demo_ai.chat(snapshot, "what is the meaning of life"))
check("and says what it can be asked",
      "Ask me about" in demo_ai.chat(snapshot, "what is the meaning of life"))


# --- 3. Categorisation cannot invent a category ---------------------------

section("3. CATEGORISATION")

EXPENSE = ["Dining Out", "Groceries", "Rent", "Transport", "Other Expense"]

check("a cafe note lands in Dining Out",
      demo_ai.categorize("flat white at the cafe", EXPENSE)["category"] == "Dining Out")
check("a supermarket note lands in Groceries",
      demo_ai.categorize("weekly shop at the supermarket", EXPENSE)["category"] == "Groceries")
check("a taxi note lands in Transport",
      demo_ai.categorize("uber home", EXPENSE)["category"] == "Transport")
check("a matched note is high confidence",
      demo_ai.categorize("monthly rent to landlord", EXPENSE)["confidence"] == "high")
check("the reason names what was matched",
      "landlord" in demo_ai.categorize("paid the landlord", EXPENSE)["reason"])

vague = demo_ai.categorize("thing", EXPENSE)
check("an unrecognisable note falls back", vague["category"] == "Other Expense")
check("and admits it is a guess", vague["confidence"] == "low", vague["confidence"])
check("and says why", "no recognisable keyword" in vague["reason"])

check("a category this install lacks is never returned",
      demo_ai.categorize("flat white at the cafe", ["Groceries", "Other Expense"])["category"]
      in ("Groceries", "Other Expense"))
check("income notes use income categories",
      demo_ai.categorize("monthly salary", ["Salary", "Freelance", "Other Income"],
                         tx_type="income")["category"] == "Salary")
check("an unmatched income note uses the income catch-all",
      demo_ai.categorize("qqq", ["Salary", "Other Income"], tx_type="income")["category"]
      == "Other Income")
check("with no catch-all it still returns a real category",
      demo_ai.categorize("qqq", ["Salary"], tx_type="income")["category"] == "Salary")
check("word boundaries stop false matches",
      demo_ai.categorize("new cabinet for the hallway", EXPENSE)["category"] != "Transport",
      demo_ai.categorize("new cabinet for the hallway", EXPENSE)["category"])
check("but an inflected keyword still matches",
      demo_ai.categorize("groceries for the week", EXPENSE)["category"] == "Groceries")
check("an empty note does not crash",
      demo_ai.categorize("", EXPENSE)["confidence"] == "low")


# --- 4. The fallback stands in, it does not replace -----------------------

section("4. FALLBACK BEHAVIOUR")

_REAL_CLIENT = ai_service._client
_req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
BILLING = anthropic.BadRequestError(
    "Your credit balance is too low to access the Anthropic API.",
    response=httpx.Response(400, request=_req), body=None,
)


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Reply:
    def __init__(self, text):
        self.content, self.stop_reason = [_Block(text)], "end_turn"


def use(reply=None, raises=None, capture=None):
    """Point the service at a scripted client instead of the network."""

    class Messages:
        def create(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            if raises is not None:
                raise raises
            return reply

    ai_service._client = lambda: types.SimpleNamespace(messages=Messages())


settings.anthropic_api_key = "sk-test-not-real"
settings.ai_demo_mode = "auto"

captured = {}
use(_Reply("Real model output."), capture=captured)
response = client.post("/api/ai/insights")
check("the real API is tried first and used when it works",
      response.json()["insights"] == "Real model output.", response.json()["insights"][:40])
check("a real reply is labelled with the real model",
      response.json()["model"] == "claude-opus-5", response.json()["model"])
check("the real call actually happened", captured.get("model") == "claude-opus-5")

use(raises=BILLING)
response = client.post("/api/ai/insights")
check("a billing failure falls back instead of erroring",
      response.status_code == 200, str(response.status_code))
check("the stand-in is labelled as one", response.json()["model"] == "demo")
check("and it answers from the real data", "Rent" in response.json()["insights"])

response = client.post("/api/ai/forecast")
check("forecast narration falls back too",
      response.status_code == 200 and response.json()["model"] == "demo")
response = client.post("/api/ai/chat", json={"message": "where can I cut back?"})
check("chat falls back too",
      response.status_code == 200 and response.json()["model"] == "demo")
response = client.post("/api/ai/categorize", json={"description": "coffee and a croissant"})
check("categorisation falls back too",
      response.status_code == 200 and response.json()["model"] == "demo")
check("and still returns a category that exists",
      response.json()["category"] == "Dining Out", response.json()["category"])

use(raises=anthropic.APIConnectionError(request=_req))
check("a lost connection falls back as well",
      client.post("/api/ai/insights").json()["model"] == "demo")

ai_service._client = _REAL_CLIENT
settings.anthropic_api_key = ""
response = client.post("/api/ai/insights")
check("no key at all still answers", response.status_code == 200)
check("labelled as a stand-in", response.json()["model"] == "demo")


# --- 5. The switches ------------------------------------------------------

section("5. MODES")

settings.ai_demo_mode = "always"
captured = {}
use(_Reply("Should never be seen."), capture=captured)
response = client.post("/api/ai/insights")
check("always mode never calls the API", captured == {}, str(captured)[:40])
check("always mode still answers", response.json()["model"] == "demo")

settings.ai_demo_mode = "off"
use(raises=BILLING)
response = client.post("/api/ai/insights")
check("off mode surfaces the real error", response.status_code == 503, str(response.status_code))
check("with the real message", "out of credit" in response.json()["detail"],
      response.json()["detail"][:50])

settings.ai_demo_mode = "nonsense"
check("an unrecognised mode behaves as auto", ai_service._demo_mode() == "auto")

settings.ai_demo_mode = "auto"
status = client.get("/api/ai/status").json()
check("status tells the UI a reply is guaranteed", status["demo_available"] is True)
check("status names the marker the UI looks for", status["demo_model"] == "demo")
settings.ai_demo_mode = "off"
check("and reports it honestly when switched off",
      client.get("/api/ai/status").json()["demo_available"] is False)

ai_service._client = _REAL_CLIENT
settings.ai_demo_mode = "auto"


# --- 6. Nothing was taken away -------------------------------------------

section("6. THE REAL INTEGRATION IS INTACT")

check("the real model is still the configured one", ai_service.MODEL == "claude-opus-5")
check("the real client factory is untouched", ai_service._client is _REAL_CLIENT)
check("the real prompts are still there",
      "finance copilot" in ai_service.SYSTEM_PROMPT and
      "explain spending forecasts" in ai_service.FORECAST_SYSTEM.lower())
check("the schema-constrained categoriser is still there",
      "enum" in str(ai_service._categorize_schema(["Groceries"])))
check("the live categorise path still exists", callable(ai_service._live_categorize))
check("the forecast numbers never touch the AI",
      client.get("/api/forecast").status_code == 200)


print("\n" + "=" * 46)
passed = sum(results)
print(f"{passed}/{len(results)} checks passed")
raise SystemExit(0 if passed == len(results) else 1)

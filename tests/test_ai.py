"""AI endpoint tests.

These run with NO real API key: a fake client replaces the network call, so the
full request path — routing, validation, prompt construction, response parsing
and error mapping — is exercised without spending a token or touching a secret.

    PYTHONPATH=. venv/bin/python tests/test_ai.py
"""

import json
import os
import pathlib
import tempfile
import types

os.environ["DATABASE_URL"] = f"sqlite:///{pathlib.Path(tempfile.mkdtemp()) / 'ai_test.db'}"

import anthropic  # noqa: E402
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ai_service  # noqa: E402

# Keep the real factory so the missing-key path can be tested for real.
_REAL_CLIENT = ai_service._client

# This file is about the real integration, so the demo fallback is switched off.
# Left on, every error path below would be answered by a stand-in instead of
# surfacing, and these checks would quietly stop testing anything. The fallback
# has its own suite in test_demo_ai.py.
settings.ai_demo_mode = "off"

PASS, FAIL = "PASS", "FAIL"
results = []


def check(label, condition, detail=""):
    results.append(condition)
    print(f"  [{PASS if condition else FAIL}] {label}" + (f" — {detail}" if detail else ""))


class FakeBlock:
    def __init__(self, text):
        self.type, self.text = "text", text


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content, self.stop_reason = [FakeBlock(text)], stop_reason


def fake_client(reply, capture=None, raises=None):
    """Stand-in for anthropic.Anthropic with a scripted reply."""

    class Messages:
        def create(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            if raises is not None:
                raise raises
            return reply

    return lambda: types.SimpleNamespace(messages=Messages())


def use(reply, capture=None, raises=None):
    settings.anthropic_api_key = "test-key-not-real"
    ai_service._client = fake_client(reply, capture, raises)


def no_key():
    """Restore the real client factory so its key check actually runs."""
    settings.anthropic_api_key = ""
    ai_service._client = _REAL_CLIENT


init_db()  # TestClient without a context manager doesn't run startup
client = TestClient(app)

# Seed a little data so insights have something to summarise.
for tx in [
    {"type": "income", "amount": 5200, "category": "Salary", "date": "2026-08-01"},
    {"type": "expense", "amount": 1800, "category": "Rent", "date": "2026-08-02"},
    {"type": "expense", "amount": 430, "category": "Dining Out", "date": "2026-08-12"},
]:
    client.post("/api/transactions", json=tx)
client.put("/api/budgets", json={"category": "Dining Out", "monthly_limit": 400})


print("\n=== 1. STATUS ENDPOINT ===")
no_key()
body = client.get("/api/ai/status").json()
check("reports not configured without a key", body["configured"] is False)
check("reports the model in use", body["model"] == "claude-opus-5", body["model"])
check("status never contains a key field", "key" not in json.dumps(body).lower(), json.dumps(body))

settings.anthropic_api_key = "test-key-not-real"
check("reports configured once a key is set", client.get("/api/ai/status").json()["configured"] is True)


print("\n=== 2. INSIGHTS (happy path) ===")
captured = {}
use(FakeResponse("Rent is 81% of your spend. Trimming Dining Out by 100 is the realistic win."), captured)
r = client.post("/api/ai/insights")
check("returns 200", r.status_code == 200, str(r.status_code))
check("advice text returned", "Rent is 81%" in r.json()["insights"])
check("model reported", r.json()["model"] == "claude-opus-5")
check("summary attached", r.json()["generated_from"]["total_income"] == "5200.00")
check("uses claude-opus-5", captured["model"] == "claude-opus-5", captured["model"])
check("real spending data reached the prompt", "1800.00" in captured["messages"][0]["content"])
check("system prompt applied", "finance copilot" in captured["system"])


print("\n=== 3. CATEGORIZATION (happy path) ===")
captured = {}
use(FakeResponse(json.dumps({"category": "Groceries", "confidence": "high", "reason": "supermarket run"})), captured)
r = client.post("/api/ai/categorize", json={"description": "weekly shop at the supermarket", "amount": 54.2})
check("returns 200", r.status_code == 200, r.text[:80])
body = r.json()
check("category returned", body["category"] == "Groceries", body.get("category"))
check("confidence returned", body["confidence"] == "high")
check("reason returned", body["reason"] == "supermarket run")
fmt = captured["output_config"]["format"]
check("structured output requested", fmt["type"] == "json_schema")
check("category constrained to an enum", "Groceries" in fmt["schema"]["properties"]["category"]["enum"])
check("only expense categories offered", "Salary" not in fmt["schema"]["properties"]["category"]["enum"])
check("amount passed as context", "54.2" in captured["messages"][0]["content"])

captured = {}
use(FakeResponse(json.dumps({"category": "Freelance", "confidence": "medium", "reason": "client payment"})), captured)
r = client.post("/api/ai/categorize", json={"description": "payment from client", "type": "income"})
check("income type offers income categories", r.json()["category"] == "Freelance", r.text[:60])
check("expense categories excluded for income",
      "Rent" not in captured["output_config"]["format"]["schema"]["properties"]["category"]["enum"])


print("\n=== 4. ERROR HANDLING ===")
no_key()
for path, payload in [("/api/ai/insights", None), ("/api/ai/chat", {"message": "hi"}),
                      ("/api/ai/categorize", {"description": "coffee"})]:
    r = client.post(path, json=payload) if payload else client.post(path)
    check(f"{path} -> 503 without a key", r.status_code == 503, str(r.status_code))
    check(f"{path} explains how to fix it", "ANTHROPIC_API_KEY" in r.json()["detail"])

_req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
cases = [
    ("invalid key",
     anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=_req), body=None),
     "rejected"),
    ("rate limit",
     anthropic.RateLimitError("slow down", response=httpx.Response(429, request=_req), body=None),
     "Rate limited"),
    ("server error",
     anthropic.InternalServerError("boom", response=httpx.Response(500, request=_req), body=None),
     "having trouble"),
    ("connection lost", anthropic.APIConnectionError(request=_req), "Could not reach"),
    ("out of credit",
     anthropic.BadRequestError(
         "Your credit balance is too low to access the Anthropic API.",
         response=httpx.Response(400, request=_req), body=None),
     "out of credit"),
]
for label, exc, expected in cases:
    use(None, raises=exc)
    r = client.post("/api/ai/insights")
    check(f"{label} -> 503 with a clear message", r.status_code == 503 and expected in r.json()["detail"],
          r.json().get("detail", "")[:60])

use(FakeResponse("", stop_reason="refusal"))
r = client.post("/api/ai/insights")
check("refusal handled, not crashed", r.status_code == 503 and "declined" in r.json()["detail"])

use(FakeResponse("this is not json at all"))
r = client.post("/api/ai/categorize", json={"description": "coffee"})
check("unparseable category reply -> 503", r.status_code == 503, str(r.status_code))

use(FakeResponse(json.dumps({"category": "Crypto Moon Bags", "confidence": "high", "reason": "x"})))
r = client.post("/api/ai/categorize", json={"description": "coffee"})
check("invented category rejected", r.status_code == 503 and "does not exist" in r.json()["detail"],
      r.json().get("detail", "")[:60])

r = client.post("/api/ai/categorize", json={"description": "   "})
check("blank description -> 422", r.status_code == 422, str(r.status_code))
r = client.post("/api/ai/categorize", json={"description": "x", "amount": -5})
check("negative amount -> 422", r.status_code == 422, str(r.status_code))


print("\n=== 5. KEY IS NEVER EXPOSED ===")
SECRET = "fake-key-value-used-only-by-this-test-12345"
settings.anthropic_api_key = SECRET
ai_service._client = fake_client(FakeResponse("advice"))
bodies = [
    client.get("/api/ai/status").text,
    client.post("/api/ai/insights").text,
    client.get("/").text,
    client.get("/api/transactions").text,
    client.get("/openapi.json").text,
]
check("no endpoint response contains the key", not any(SECRET in b for b in bodies))

ai_service._client = fake_client(None, raises=anthropic.AuthenticationError(
    f"invalid x-api-key {SECRET}", response=httpx.Response(401, request=_req), body=None))
r = client.post("/api/ai/insights")
check("key not leaked in an auth error message", SECRET not in r.text, r.text[:90])

root = pathlib.Path(__file__).resolve().parent.parent
tracked = [f for f in root.rglob("*") if f.is_file()
           and not any(part in {"venv", "__pycache__", ".git", "data", "tests"} for part in f.parts)
           and f.name != ".env"]
frontend = [f for f in tracked if f.suffix in {".js", ".html", ".css"}]
check("no frontend file mentions ANTHROPIC_API_KEY as a value",
      not any("sk-ant" in f.read_text(errors="ignore") for f in frontend))
check("key only read via settings in source",
      all("os.environ" not in f.read_text() or "ANTHROPIC" not in f.read_text()
          for f in tracked if f.suffix == ".py"))
check(".env is gitignored", ".env" in (root / ".gitignore").read_text())

print("\n" + "=" * 46)
print(f"{sum(results)}/{len(results)} checks passed")
raise SystemExit(0 if all(results) else 1)

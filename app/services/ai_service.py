"""Talks to the Claude API and turns spending data into advice."""

import json
import logging

import anthropic

from app.config import settings
from app.services import demo_ai

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"

# Raised to the router so it can answer with a helpful 503 instead of a stack trace.
class AIUnavailable(RuntimeError):
    pass


SYSTEM_PROMPT = """You are a personal finance copilot built into a spending tracker.

You are given the user's own transaction data as a JSON snapshot: totals, spend by \
category, a monthly trend, and any budgets they set. All amounts are in the user's \
own currency — never name a currency or add a symbol, just use the numbers.

How to answer:
- Ground every claim in the snapshot. Quote real categories and real numbers.
- If the data cannot answer the question, say so plainly and say what to log instead.
- Be concrete: "Dining Out is 32% of your spend" beats "consider reducing discretionary \
spending".
- Keep it short. A few sentences, or a handful of bullets. No preamble, no sign-off.
- Suggest, don't scold. The user is tracking their money, which already deserves credit.
- You are not a licensed advisor: no tax, legal, or specific investment-product advice. \
Point them to a professional for those.
- Plain text only, no markdown headers or tables."""

INSIGHTS_PROMPT = """Here is my financial data:

{snapshot}

Give me a short read on where my money is going. Cover:
1. One thing that stands out in my spending.
2. The single most realistic place I could save, with the amount involved.
3. Any budget I am close to or over.

If there is barely any data yet, say so and tell me what to track first."""

CHAT_PROMPT = """Here is my financial data for reference:

{snapshot}

My question: {question}"""


def _client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise AIUnavailable(
            "No ANTHROPIC_API_KEY set. Add your key to the .env file and restart "
            "the server to turn on AI insights and chat."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _format_snapshot(snapshot: dict) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True)


def _call(
    messages: list[dict],
    max_tokens: int = 1024,
    system: str = SYSTEM_PROMPT,
    output_config: dict | None = None,
) -> str:
    """One Claude call, with API failures translated into AIUnavailable."""
    client = _client()
    # Advice on a small JSON blob is not a reasoning-heavy task; low effort
    # keeps the dashboard snappy and the cost down.
    config = output_config or {"effort": "low"}
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            output_config=config,
            messages=messages,
        )
    except anthropic.AuthenticationError as exc:
        raise AIUnavailable("The ANTHROPIC_API_KEY was rejected. Check the key.") from exc
    except anthropic.RateLimitError as exc:
        raise AIUnavailable("Rate limited by the AI API. Try again in a moment.") from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            logger.exception("Claude API returned %s", exc.status_code)
            raise AIUnavailable("The AI API is having trouble. Try again shortly.") from exc
        # A 4xx is a key, quota or billing problem rather than a bug. The message
        # below already says what to do about it, so a stack trace on every click
        # is just noise — especially with the demo fallback catching it anyway.
        logger.warning("Claude API returned %s: %s", exc.status_code, exc.message)
        # An empty balance is the most common 400 here, and the raw error body is
        # not something to put in front of someone reading a dashboard.
        if "credit balance" in str(exc).lower():
            raise AIUnavailable(
                "The Anthropic account is out of credit. Add some at "
                "console.anthropic.com/settings/billing to switch the AI features "
                "back on. Everything else on the dashboard still works."
            ) from exc
        raise AIUnavailable(f"The AI API rejected the request: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIUnavailable("Could not reach the AI API. Check your connection.") from exc

    if response.stop_reason == "refusal":
        raise AIUnavailable("The AI declined to answer that one. Try rephrasing.")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise AIUnavailable("The AI returned an empty response. Try again.")
    return text


DEMO_MODEL = "demo"


def _demo_mode() -> str:
    """auto | always | off. Anything unrecognised is treated as auto."""
    mode = (settings.ai_demo_mode or "auto").strip().lower()
    return mode if mode in {"auto", "always", "off"} else "auto"


def demo_available() -> bool:
    """Whether a failed call will still produce an answer."""
    return _demo_mode() != "off"


def _resolve(live, demo) -> tuple[str, str]:
    """Run the real call, dropping to a built-in demo reply if it cannot be made.

    This is what keeps the app demonstrable without an API key or a credit
    balance. The real integration is untouched — it is tried first and only
    stood in for when it raises — and the model name returned says which path
    answered, so the dashboard can badge a stand-in rather than pass it off.
    """
    mode = _demo_mode()
    if mode == "always":
        return demo(), DEMO_MODEL
    try:
        return live(), MODEL
    except AIUnavailable as exc:
        if mode == "off":
            raise
        logger.warning("AI unavailable (%s) — answering with a demo reply.", exc)
        return demo(), DEMO_MODEL


def generate_insights(snapshot: dict) -> tuple[str, str]:
    """Unprompted advice for the dashboard. Returns (text, model)."""

    def live() -> str:
        prompt = INSIGHTS_PROMPT.format(snapshot=_format_snapshot(snapshot))
        return _call([{"role": "user", "content": prompt}])

    return _resolve(live, lambda: demo_ai.insights(snapshot))


def answer_question(
    snapshot: dict, question: str, history: list[dict] | None = None
) -> tuple[str, str]:
    """Free-form chat about the user's money. Returns (reply, model).

    The snapshot rides on the newest user turn only, so earlier turns in the
    history stay small and the model always sees current numbers.
    """

    def live() -> str:
        messages = list(history or [])
        messages.append(
            {
                "role": "user",
                "content": CHAT_PROMPT.format(
                    snapshot=_format_snapshot(snapshot), question=question
                ),
            }
        )
        return _call(messages)

    return _resolve(live, lambda: demo_ai.chat(snapshot, question))


# --- Expense categorization ------------------------------------------------

CATEGORIZE_SYSTEM = """You label personal finance transactions.

Given a short free-text note about a transaction, pick the single best-fitting \
category from the list you are given. Rules:
- You must choose from the provided list. Never invent a category.
- Judge only from the note and amount. Do not guess wildly.
- Set confidence to "low" when the note is vague, ambiguous, or could fit several \
categories equally well; "high" only when the note clearly names the spending.
- reason is at most 8 words explaining the choice."""

CATEGORIZE_PROMPT = """Categories to choose from: {categories}

Transaction type: {tx_type}
Note: {description}{amount_line}

Which category fits best?"""


def _categorize_schema(categories: list[str]) -> dict:
    """Constrain the reply to a real category, so no validation can fail later."""
    return {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                # enum: the model physically cannot return a category we don't have.
                "category": {"type": "string", "enum": categories},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "reason": {"type": "string", "maxLength": 60},
            },
            "required": ["category", "confidence", "reason"],
            "additionalProperties": False,
        },
    }


def _live_categorize(
    description: str, categories: list[str], tx_type: str, amount: float | None
) -> dict:
    """Ask the model to pick a category. Returns the parsed reply, no model name."""
    prompt = CATEGORIZE_PROMPT.format(
        categories=", ".join(categories),
        tx_type=tx_type,
        description=description.strip(),
        amount_line=f"\nAmount: {amount}" if amount is not None else "",
    )

    raw = _call(
        [{"role": "user", "content": prompt}],
        max_tokens=200,
        system=CATEGORIZE_SYSTEM,
        output_config={"effort": "low", "format": _categorize_schema(categories)},
    )

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIUnavailable("The AI returned a category that could not be read.") from exc

    # The schema already constrains this, but a wrong category would silently
    # write bad data, so re-check rather than trust.
    if result.get("category") not in categories:
        raise AIUnavailable("The AI suggested a category that does not exist.")

    return result


def suggest_category(
    description: str,
    categories: list[str],
    tx_type: str = "expense",
    amount: float | None = None,
) -> dict:
    """Pick the best category for a transaction note. Returns the parsed reply.

    Falls back to keyword matching on the note when the API cannot be reached.
    Both paths can only return a category this install actually has: the real
    one because the reply is constrained to a JSON-schema enum, the stand-in
    because it picks from the same list.
    """
    if not categories:
        raise AIUnavailable("No categories are configured to choose from.")

    mode = _demo_mode()
    if mode != "always":
        try:
            result = _live_categorize(description, categories, tx_type, amount)
            result["model"] = MODEL
            return result
        except AIUnavailable as exc:
            if mode == "off":
                raise
            logger.warning("AI unavailable (%s) — matching the note on keywords.", exc)

    result = demo_ai.categorize(description, categories, tx_type)
    result["model"] = DEMO_MODEL
    return result


def is_configured() -> bool:
    """Whether AI features can run at all — the UI uses this to set expectations."""
    return bool(settings.anthropic_api_key)


# --- Forecast narration ----------------------------------------------------

FORECAST_SYSTEM = """You explain spending forecasts inside a personal finance tracker.

The forecast has already been calculated from the user's own transactions by the \
app itself, in plain Python. Your job is to explain it, not to redo it.

Rules:
- Use only figures that appear in the forecast. Never compute, estimate or invent \
a number, and never contradict one that is there.
- Keep "spent so far" and "projected" distinct. A projection is where a month is \
heading, not what has already left the account.
- Respect the confidence fields. Saying a low-confidence forecast is a rough middle \
is more useful than sounding certain about it.
- Amounts are in the user's own currency — never name a currency or add a symbol.
- Keep it short. A few sentences, or a handful of bullets. No preamble, no sign-off.
- Suggest, don't scold.
- You are not a licensed advisor: no tax, legal, or specific investment-product \
advice. Point them to a professional for those.
- Plain text only, no markdown headers or tables."""

FORECAST_PROMPT = """Here is my spending forecast, already calculated from my \
transaction history:

{forecast}

Explain what this means for me. Cover:
1. Where this month is heading, and whether that is normal for me.
2. The one category most worth watching, and what moved it.
3. Any budget projected to go over, and what it would take to stay inside it.

If there is little history or the confidence is low, say so plainly rather than \
dressing up a guess."""


def explain_forecast(forecast: dict) -> tuple[str, str]:
    """Turn the computed forecast into plain English. Returns (text, model).

    The numbers are settled before this is called; nothing here can change them.
    """

    def live() -> str:
        prompt = FORECAST_PROMPT.format(forecast=_format_snapshot(forecast))
        return _call([{"role": "user", "content": prompt}], system=FORECAST_SYSTEM)

    return _resolve(live, lambda: demo_ai.forecast_narrative(forecast))

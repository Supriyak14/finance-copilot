"""Stand-in AI replies for when the Anthropic API cannot be reached.

The point of this module is that the whole app stays demonstrable — expense
categorisation, spending insights, chat and the forecast write-up — with no API
key and no credit balance behind it.

Nothing here is a language model. Every reply is composed from the user's own
figures with ordinary Python, which is why the text moves when the data moves
instead of being one canned paragraph. Replies are labelled as demo output all
the way through to the browser, which badges them: a stand-in should never be
mistaken for the real thing.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

# Spending that cannot realistically be cut this month, so it is never offered
# as the place to save.
ESSENTIALS = {"rent", "utilities", "subscriptions", "health", "education", "insurance"}

NOTHING_LOGGED = (
    "There is nothing logged yet, so there is nothing worth reading into.\n\n"
    "Start with the things you pay for without thinking — rent, a weekly shop, "
    "transport, any subscription that renews on its own. A fortnight of that is "
    "enough for the charts and the forecast to say something useful."
)


def _amount(value) -> Decimal:
    """Snapshot numbers arrive as JSON strings, and may be missing entirely."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _money(value) -> str:
    return f"{_amount(value):,.2f}"


def _month_name(key: str) -> str:
    """'2026-09' -> 'September'."""
    try:
        year, month = (int(part) for part in str(key).split("-"))
        return date(year, month, 1).strftime("%B")
    except (ValueError, TypeError):
        return "later"


def _join(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _first_number(text: str) -> Decimal | None:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    return _amount(match.group(0).replace(",", ""))


def _position(net) -> str:
    """'620.00 up' / '150.00 down' / 'break-even'."""
    net = _amount(net)
    if net > 0:
        return f"{_money(net)} up"
    if net < 0:
        return f"{_money(-net)} down"
    return "break-even"


def _discretionary(rows: list[dict]) -> dict | None:
    """The biggest category that could actually be cut back."""
    return next((r for r in rows if r["category"].lower() not in ESSENTIALS), None)


# --- Spending insights -----------------------------------------------------


def insights(snapshot: dict) -> str:
    overall = snapshot.get("overall") or {}
    top = snapshot.get("top_expense_categories") or []
    budgets = snapshot.get("budgets") or []

    if not top:
        return NOTHING_LOGGED

    paragraphs = []

    lead = top[0]
    opening = (
        f"{lead['category']} is your largest outgoing at {_money(lead['total'])}, "
        f"{lead['share']}% of everything you have spent."
    )
    if len(top) > 1:
        opening += (
            f" {top[1]['category']} follows at {_money(top[1]['total'])} "
            f"({top[1]['share']}%)."
        )
    paragraphs.append(opening)

    saver = _discretionary(top)
    if saver is not None:
        trim = (_amount(saver["total"]) * Decimal("0.20")).quantize(Decimal("0.01"))
        paragraphs.append(
            f"The most realistic place to save is {saver['category']} — it is "
            f"discretionary, and a fifth off it is about {_money(trim)}. That is a "
            f"change you would barely notice week to week."
        )

    over = [b for b in budgets if b.get("status") == "over"]
    warning = [b for b in budgets if b.get("status") == "warning"]
    if over:
        first = over[0]
        paragraphs.append(
            f"{_join([b['category'] for b in over])} "
            f"{'is' if len(over) == 1 else 'are'} over budget this month. "
            f"{first['category']} alone is at {_money(first['spent'])} against a "
            f"{_money(first['monthly_limit'])} cap."
        )
    elif warning:
        first = warning[0]
        paragraphs.append(
            f"{first['category']} has used {first['percent_used']}% of its "
            f"{_money(first['monthly_limit'])} budget. Worth watching, but nothing "
            f"has broken yet."
        )
    elif budgets:
        paragraphs.append("Every budget you have set is still inside its cap.")

    if _amount(overall.get("total_income")) > 0:
        rate = overall.get("savings_rate", 0)
        if rate >= 20:
            verdict = "which is a genuinely healthy margin"
        elif rate >= 10:
            verdict = "which is reasonable, with room to push higher"
        elif rate > 0:
            verdict = "which is thin — most of what comes in goes straight back out"
        else:
            verdict = "meaning you are spending more than you bring in"
        paragraphs.append(
            f"Across everything logged you have kept {rate}% of your income, {verdict}."
        )

    return "\n\n".join(paragraphs)


# --- Chat ------------------------------------------------------------------

SAVE_WORDS = ("save", "saving", "cut", "reduce", "spend less", "cheaper", "budget better")
AFFORD_WORDS = ("afford", "can i buy", "should i buy", "enough for")
INCOME_WORDS = ("income", "earn", "salary", "how much do i make", "take home")


def chat(snapshot: dict, question: str) -> str:
    asked = (question or "").lower()
    overall = snapshot.get("overall") or {}
    this_month = snapshot.get("this_month") or {}
    top = snapshot.get("top_expense_categories") or []
    budgets = snapshot.get("budgets") or []

    if not top:
        return NOTHING_LOGGED

    # A named category is the most specific thing they could have asked about.
    for row in top:
        if row["category"].lower() in asked:
            reply = (
                f"You have spent {_money(row['total'])} on {row['category']}, "
                f"{row['share']}% of your total spending."
            )
            budget = next((b for b in budgets if b["category"] == row["category"]), None)
            if budget:
                reply += (
                    f" This month it is at {_money(budget['spent'])} against a "
                    f"{_money(budget['monthly_limit'])} budget — "
                    f"{budget['percent_used']}% used."
                )
            return reply

    if any(word in asked for word in AFFORD_WORDS):
        net = _amount(this_month.get("net"))
        wanted = _first_number(asked)
        if wanted is None:
            return (
                f"Right now you are {_position(net)} this month. Tell me roughly "
                f"what it costs and I can say whether it fits."
            )
        if net > 0 and wanted <= net:
            return (
                f"Yes, with room to spare. You are {_position(net)} this month, so "
                f"{_money(wanted)} would still leave {_money(net - wanted)}."
            )
        return (
            f"Not out of this month alone. You are {_position(net)} this month and "
            f"it would cost {_money(wanted)}, leaving you {_money(wanted - net)} "
            f"short. Spreading it over two months, or trimming "
            f"{(_discretionary(top) or top[0])['category']}, would close the gap."
        )

    if "savings rate" in asked or "saving rate" in asked:
        return (
            f"Your savings rate is {overall.get('savings_rate', 0)}% — you have "
            f"kept {_money(overall.get('net'))} of {_money(overall.get('total_income'))} "
            f"in income."
        )

    if any(word in asked for word in SAVE_WORDS):
        saver = _discretionary(top) or top[0]
        trim = (_amount(saver["total"]) * Decimal("0.20")).quantize(Decimal("0.01"))
        return (
            f"{saver['category']} is where the room is: {_money(saver['total'])} so "
            f"far, {saver['share']}% of your spending. A fifth off it is about "
            f"{_money(trim)}, and it is discretionary enough that cutting it does "
            f"not change how you live."
        )

    if "budget" in asked:
        if not budgets:
            return (
                "You have not set any budgets yet. Pick the two or three "
                "categories you most want to control — "
                f"{_join([r['category'] for r in top[:3]])} would be the obvious "
                "ones — and set a monthly cap on each."
            )
        over = [b for b in budgets if b.get("status") == "over"]
        warning = [b for b in budgets if b.get("status") == "warning"]
        if over:
            return (
                f"{_join([b['category'] for b in over])} "
                f"{'is' if len(over) == 1 else 'are'} over. "
                f"{over[0]['category']} is at {_money(over[0]['spent'])} against "
                f"{_money(over[0]['monthly_limit'])}."
            )
        if warning:
            return (
                f"Nothing is over yet, but {_join([b['category'] for b in warning])} "
                f"{'is' if len(warning) == 1 else 'are'} past 80% of the cap."
            )
        return "All of your budgets are comfortably inside their caps this month."

    if any(word in asked for word in INCOME_WORDS):
        return (
            f"You have logged {_money(overall.get('total_income'))} of income "
            f"against {_money(overall.get('total_expense'))} of spending, leaving "
            f"{_money(overall.get('net'))} net."
        )

    trend = snapshot.get("monthly_trend") or []
    if trend and any(word in asked for word in ("month", "trend", "recent", "lately")):
        latest = trend[-1]
        return (
            f"In {_month_name(latest['month'])} you brought in "
            f"{_money(latest['income'])} and spent {_money(latest['expense'])}, "
            f"a net of {_money(latest['net'])}. That is across "
            f"{len(trend)} month{'' if len(trend) == 1 else 's'} of history."
        )

    return (
        f"Across everything logged: {_money(overall.get('total_income'))} in, "
        f"{_money(overall.get('total_expense'))} out, {_money(overall.get('net'))} "
        f"net, with {top[0]['category']} your biggest single cost.\n\n"
        f"Ask me about a category by name, whether you can afford something, where "
        f"you could cut back, or how your budgets are doing."
    )


# --- Forecast narration ----------------------------------------------------


def forecast_narrative(forecast: dict) -> str:
    month = forecast.get("current_month") or {}
    categories = forecast.get("categories") or []
    risks = forecast.get("budget_risk") or []
    horizon = forecast.get("horizon") or []
    months = forecast.get("months_of_history", 0)

    if not categories:
        return (
            "There is not a complete month of history yet, so there is nothing "
            "solid to project from. Once a full month is logged this will start "
            "reporting where the next one is heading."
        )

    paragraphs = []

    spent = _amount(month.get("expense_so_far"))
    projected = _amount(month.get("projected_expense"))
    elapsed = month.get("days_elapsed", 0)
    total_days = month.get("days_in_month", 30)
    paragraphs.append(
        f"You are {elapsed} days into a {total_days}-day month with "
        f"{_money(spent)} spent. On current habits it ends around "
        f"{_money(projected)} — about {_money(projected - spent)} still to come — "
        f"for a projected net of {_money(month.get('projected_net'))}."
    )

    expenses = [c for c in categories if c.get("type") == "expense"]
    rising = [c for c in expenses if c.get("trend") == "rising"]
    if rising:
        watch = max(rising, key=lambda c: _amount(c["change"]))
        hedge = (
            f", though only at {watch['confidence']} confidence"
            if watch.get("confidence") != "high"
            else ""
        )
        paragraphs.append(
            f"{watch['category']} is the one to watch. It has averaged "
            f"{_money(watch['average'])} a month and is forecast at "
            f"{_money(watch['forecast'])} next month, up "
            f"{watch['change_percent']}%{hedge}."
        )
    elif expenses:
        biggest = expenses[0]
        tail = (
            " — a fixed bill, so it is not where the flexibility is."
            if biggest.get("kind") == "fixed"
            else "."
        )
        paragraphs.append(
            f"Nothing is climbing sharply. {biggest['category']} is simply the "
            f"largest line, forecast at {_money(biggest['forecast'])}{tail}"
        )

    breaking = [r for r in risks if r.get("status") == "will_exceed"]
    close = [r for r in risks if r.get("status") == "at_risk"]
    if breaking:
        first = breaking[0]
        left = _amount(first["monthly_limit"]) - _amount(first["spent_so_far"])
        days_left = max(total_days - elapsed, 0)
        if left > 0 and days_left > 0:
            weekly = (left / days_left * 7).quantize(Decimal("0.01"))
            advice = (
                f"Holding it to about {_money(weekly)} a week for the rest of the "
                f"month would bring it back inside."
            )
        else:
            advice = (
                "That cap is already spent, so the question is how far over it "
                "goes rather than whether."
            )
        paragraphs.append(
            f"{_join([r['category'] for r in breaking])} "
            f"{'is' if len(breaking) == 1 else 'are'} projected to finish over "
            f"budget. {first['category']} is the widest miss at "
            f"{_money(first['projected_spend'])} against "
            f"{_money(first['monthly_limit'])}, over by "
            f"{_money(first['projected_overage'])}. {advice}"
        )
    elif close:
        paragraphs.append(
            f"{_join([r['category'] for r in close])} "
            f"{'is' if len(close) == 1 else 'are'} projected to finish just inside "
            f"the cap — close enough that one unplanned purchase would tip it over."
        )
    elif risks:
        paragraphs.append("Every budget is projected to finish inside its cap.")

    if forecast.get("confidence") != "high" or months < 3:
        paragraphs.append(
            f"Treat this as a rough steer. It is built on {months} complete "
            f"month{'' if months == 1 else 's'} of history, which is "
            f"{forecast.get('confidence', 'low')} confidence."
        )
    elif horizon:
        last = horizon[-1]
        paragraphs.append(
            f"Further out, the next {len(horizon)} months project to a cumulative "
            f"{_money(last['cumulative_net'])} by the end of "
            f"{_month_name(last['month'])}."
        )

    return "\n\n".join(paragraphs)


# --- Expense categorisation ------------------------------------------------

# Ordered specific to general: the first category whose keyword appears wins, so
# "concert ticket" lands in Entertainment rather than Transport.
KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Rent", ("rent", "landlord", "lease", "tenancy")),
    ("Utilities", ("electric", "water bill", "gas bill", "internet", "broadband",
                   "utility", "utilities", "council tax", "heating")),
    ("Subscriptions", ("subscription", "netflix", "spotify", "icloud", "membership",
                       "renewal", "storage plan")),
    ("Travel", ("flight", "hotel", "airbnb", "holiday", "vacation", "airline",
                "boarding", "hostel", "trip")),
    ("Health", ("pharmacy", "chemist", "doctor", "dentist", "clinic", "medicine",
                "prescription", "hospital", "optician", "physio")),
    ("Education", ("course", "tuition", "textbook", "semester", "exam fee",
                   "university", "college fee")),
    ("Entertainment", ("cinema", "movie", "concert", "theatre", "museum", "bowling",
                       "festival", "streaming rental")),
    ("Dining Out", ("coffee", "cafe", "latte", "restaurant", "dinner", "lunch",
                    "breakfast", "brunch", "takeaway", "take away", "pizza",
                    "burger", "sushi", "snack", "dessert")),
    ("Groceries", ("grocer", "supermarket", "weekly shop", "vegetable", "milk",
                   "bread", "butcher", "greengrocer")),
    ("Transport", ("uber", "taxi", "cab", "bus", "train", "metro", "tram", "fuel",
                   "petrol", "diesel", "parking", "fare", "ticket", "commute")),
    ("Shopping", ("clothes", "clothing", "shoes", "shirt", "jacket", "amazon",
                  "electronics", "headphones", "furniture", "household")),
    ("Salary", ("salary", "payroll", "paycheck", "pay check", "wages", "monthly pay")),
    ("Freelance", ("freelance", "invoice", "client", "contract work", "commission")),
    ("Investments", ("dividend", "interest", "stock", "shares", "mutual fund")),
    ("Gifts", ("gift", "present", "birthday money")),
]


def _mentions(note: str, keyword: str) -> bool:
    """Whole-word match, with a short inflection allowed on longer keywords.

    That is what lets "grocer" catch "groceries" while "cab" stays off
    "cabinet" — anchoring only the start of the word would match both.
    """
    inflection = r"\w{0,3}" if len(keyword) >= 4 else ""
    return re.search(rf"\b{re.escape(keyword)}{inflection}\b", note) is not None


def categorize(description: str, categories: list[str], tx_type: str = "expense") -> dict:
    """Pick a category from keywords in the note.

    `categories` is the list this install actually has, already filtered to the
    right type, and the return is always one of them — the same guarantee the
    real, schema-constrained path gives.
    """
    note = (description or "").lower()
    available = set(categories)

    for category, keywords in KEYWORDS:
        if category not in available:
            continue
        hit = next((word for word in keywords if _mentions(note, word)), None)
        if hit is not None:
            return {
                "category": category,
                "confidence": "high",
                "reason": f'the note mentions "{hit}"',
            }

    # Nothing recognisable. Land on the catch-all and say so, rather than
    # picking something plausible-looking and being confidently wrong.
    fallback = "Other Income" if tx_type == "income" else "Other Expense"
    if fallback not in available:
        fallback = categories[0]
    return {
        "category": fallback,
        "confidence": "low",
        "reason": "no recognisable keyword in the note",
    }

# AI Personal Finance Copilot

Track spending, visualize it, forecast where the month is heading, and get
AI-generated money advice.

**Live demo:** _paste your public URL here once deployed_ — no login, no API key.

Stack: FastAPI + SQLite + vanilla HTML/CSS/JS + Chart.js

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then paste your key into ANTHROPIC_API_KEY
```

To look around with something already in it:

```bash
python scripts/seed_demo.py           # ~7 months of realistic transactions + budgets
python scripts/seed_demo.py --reset   # replace whatever is there now
```

The draws are seeded, so the same history — and the same forecast — comes back
every time.

Everything except the AI features works without a key — and the AI features
themselves still answer, using the built-in demo replies described under
[Demo mode](#demo-mode). Nothing on the dashboard is a dead end.

## Run

```bash
uvicorn app.main:app --reload
```

- Dashboard — http://127.0.0.1:8000
- Interactive API docs — http://127.0.0.1:8000/docs

Tables are created and the category picklist is seeded on startup, so the first
run needs no migration step.

## API

| Method | Path | What it does |
| --- | --- | --- |
| `GET` | `/api/transactions` | List, filtered by `type`, `category`, `start`, `end`; paged with `limit`/`offset` |
| `POST` | `/api/transactions` | Add one |
| `GET` | `/api/transactions/{id}` | Fetch one |
| `PATCH` | `/api/transactions/{id}` | Change only the fields you send |
| `DELETE` | `/api/transactions/{id}` | Remove one |
| `GET` | `/api/categories` | Picklist, optionally filtered by `type` |
| `GET` | `/api/analytics/summary` | Income, expense, net, savings rate |
| `GET` | `/api/analytics/by-category` | Category totals and shares (doughnut chart) |
| `GET` | `/api/analytics/monthly` | Income vs expense per month (bar chart) |
| `GET` | `/api/budgets` | List budgets |
| `PUT` | `/api/budgets` | Create or update a category's monthly cap |
| `GET` | `/api/budgets/status` | Each budget vs this month's spend, with `ok`/`warning`/`over` |
| `DELETE` | `/api/budgets/{id}` | Remove a budget |
| `GET` | `/api/forecast` | Month-end projection, per-category forecasts, months ahead, recurring bills, budget risk. `horizon` and `lookback` are tunable |
| `GET` | `/api/ai/status` | Whether an API key is configured |
| `POST` | `/api/ai/insights` | Advice generated from your own numbers |
| `POST` | `/api/ai/chat` | Ask a question; send prior turns as `history` |
| `POST` | `/api/ai/categorize` | Suggest a category for a transaction note |
| `POST` | `/api/ai/forecast` | Explain the forecast in plain English |

## Forecasting

`GET /api/forecast` answers three questions: where is this month landing, what
will the next few cost, and which budgets are going to break.

All of it is plain Python — no model is involved, so the numbers are
deterministic, unit-testable, cost nothing, and work with no API key at all.

**How the method works.** Only *complete* months are learned from; the month in
progress is a partial total and would drag every average down. Within that
window each category is sorted into one of two kinds:

- **Recurring bills** — same amount, most months, at least three of them. Rent
  and subscriptions land here. They are forecast at their *median*, so the one
  month something was billed twice doesn't get baked into every future month.
- **Variable spending** — forecast as a recency-weighted average (the most
  recent month counts six times as much as the oldest of six) nudged by a
  least-squares trend line. The trend is **halved** before it is applied: fitted
  to a handful of points it is a weak signal, and following it at full strength
  turns a mild drift into an alarming cliff.

**The month in progress** is projected as *what you have already spent, plus the
share of a normal month still ahead*. On day 1 that is almost entirely history;
on the last day it is simply what happened. Bills are exempt from that scaling —
one lands once, whether or not it already has.

**Why the budget warnings differ from the Budgets card.** That card reports what
has been spent so far. The forecast reports where the month *ends*. A category
at 405 of a 520 cap on the 22nd looks comfortable and is in fact heading for 551.
Statuses are `on_track`, `at_risk` (projected within 10% of the cap) and
`will_exceed`.

Every category carries a `confidence` of high, medium or low, derived from how
many months it appears in and how much it swings; the response also carries
`notes` explaining any caveat worth showing next to the numbers. A brand-new
database returns zeros and a note saying so rather than an error.

## AI features

Model: `claude-opus-5`, via the official `anthropic` Python SDK.

- **Spending insights** — `POST /api/ai/insights` sends a snapshot of your totals,
  category shares, monthly trend and budget status, and returns plain-text advice.
  The "Analyze my spending" button on the dashboard calls it.
- **Expense categorization** — `POST /api/ai/categorize` takes a free-text note and
  suggests a category. The candidate list is read from the database and passed as a
  JSON-schema `enum`, so the model can only return a category you actually have.
  The "Suggest category with AI" button under the Note field calls it.
- **Forecast narration** — `POST /api/ai/forecast` explains the forecast. The
  numbers are computed first, in Python, and handed over finished; the model is
  told to explain them and never to recompute one. So the forecast on screen and
  the write-up under it cannot disagree, and the forecast itself keeps working
  when the AI does not.

Every one of these falls back to a demo reply rather than an error when the API
is unreachable — see [Demo mode](#demo-mode).

The key is read server-side from `.env` via `app/config.py` and is never sent to the
browser: the frontend only ever talks to your own `/api/ai/*` endpoints. `.env` is
gitignored, and `/api/ai/status` reports only a boolean.

Every failure mode returns `503` with a readable message rather than a stack trace:
missing key, rejected key, rate limit, server error, lost connection, model refusal,
and a suggested category that doesn't exist.

### Testing the AI endpoints without a key

```bash
PYTHONPATH=. venv/bin/python tests/test_ai.py        # 42 checks
PYTHONPATH=. venv/bin/python tests/test_forecast.py  # 94 checks
PYTHONPATH=. venv/bin/python tests/test_demo_ai.py   # 68 checks
```

`test_ai.py` covers routing, prompt construction, response parsing, every error
path, and that the key never appears in any response. A fake client replaces the
network call, so it spends nothing and needs no key.

`test_forecast.py` pins the arithmetic down exactly: it builds a known history,
freezes "today", and checks the number that comes out — including that a paid
bill is not counted twice, that a budget under its cap today is still flagged if
the projection breaks it, and that an empty database returns zeros rather than an
error.

`test_demo_ai.py` covers the fallback: that the demo replies actually quote the
user's own numbers rather than being canned, that categorisation can never
return a category the database lacks, that the real API is tried first and used
when it works, that a stand-in is always labelled, and that `AI_DEMO_MODE=off`
restores the original errors exactly.

To exercise the AI for real, put a key in `.env` with credit on it, restart, and
use the buttons on the dashboard.

## Demo mode

The AI features never leave a dead button. When the Anthropic API cannot be
reached — no key, no credit, no wifi, rate limited — the request falls back to a
stand-in reply and the dashboard badges it **demo response** in amber.

The real integration is untouched: it is tried first on every request, and only
stood in for when it raises. `AI_DEMO_MODE` decides what happens then:

| Value | Behaviour |
| --- | --- |
| `auto` *(default)* | Try the real API, fall back to a demo reply if it fails |
| `always` | Never call the API. Instant, and cannot fail on the day |
| `off` | No fallback — surface the real error, as before |

For a live demonstration, use `always`. A fallback in `auto` still has to wait
for the API to refuse it first (~0.5s per click); `always` answers in ~0.02s and
does not care whether the room has working wifi.

```bash
AI_DEMO_MODE=always uvicorn app.main:app --reload
```

**The demo replies are not canned text.** Nothing in `demo_ai.py` is a language
model, but nothing in it is a fixed paragraph either — every reply is composed
from your own figures in plain Python, so it moves when the data moves:

- *Insights* name your actual largest category and its real share, pick the
  biggest **discretionary** category to suggest cutting (never rent or
  utilities), and report which budgets are actually broken.
- *Chat* answers the question asked — a category by name, whether you can
  afford a number you type, where to cut back, how budgets are doing, or your
  income — all quoting real totals.
- *Categorisation* matches keywords in the note against the category list read
  from your database, so like the real path it can only ever return a category
  you actually have. An unrecognisable note lands in `Other Expense` at **low**
  confidence rather than guessing confidently.
- *Forecast narration* reads the computed forecast and explains it — days
  elapsed, where the month lands, the category that moved, and what weekly
  spend would bring a broken budget back inside.

A stand-in is always labelled as one, in the API (`"model": "demo"`) and in the
UI. It is never passed off as the real model.

## Deploying the demo

The whole project is one service: FastAPI serves the API *and* renders the
dashboard, and Chart.js is vendored locally, so there is no separate frontend to
host and nothing is fetched from a CDN. That makes it a single Docker image.

**It cannot cost anything.** The image sets `AI_DEMO_MODE=always`, so the AI
features answer from the built-in stand-in and the Anthropic API is never
called. No key is baked into the image, and none is needed. `.env` is excluded
by both `.gitignore` and `.dockerignore`, and the `Dockerfile` copies named
directories rather than `COPY . .`, so it cannot be included by accident.

**The database rebuilds itself.** Hosting platforms wipe the container
filesystem on restart, so `SEED_DEMO_DATA=true` tells the app to build the
sample history at startup — but only when nothing has been logged yet, so a
real database is never overwritten.

### Deploying on Render (free, no card)

1. Push this repository to GitHub. In VS Code: **Source Control** → **Publish
   Branch** → choose **Public**. (`.env` is gitignored, so your key stays local.)
2. Sign up at [render.com](https://render.com) with **Continue with GitHub**.
3. **New** → **Blueprint** → pick this repository → **Apply**.
   `render.yaml` supplies every setting, so there is nothing to fill in.
4. Wait for the first build (~3–5 minutes), then open the
   `https://<name>.onrender.com` URL Render shows you.

Anyone can open that link on any phone or laptop. There is no login.

### Alternative: Hugging Face Spaces (free, no GitHub needed)

Create a **Space** at [huggingface.co/new-space](https://huggingface.co/new-space),
choose **Docker → Blank**, set visibility **Public**, then upload the project
files in the browser. Add `app_port: 8000` to the Space's README front matter so
it knows which port to serve.

### Limitations worth knowing

- **The free tier sleeps.** Render's free plan idles a service after ~15 minutes
  of no traffic; the next visit takes roughly 50 seconds to wake. Open the URL a
  minute before a live demonstration and it will be warm.
- **Data resets on restart.** The container filesystem is temporary, so anything
  added through the deployed app disappears when the service sleeps or
  redeploys, and the sample history comes back. For a demonstration that is
  usually what you want; it also means visitors cannot spoil it for each other
  permanently. A hosted Postgres would be the fix if persistence were needed.
- **Everyone shares one database.** There are no user accounts, so two people
  editing at the same time see each other's changes.
- **The AI replies are the built-in stand-in**, labelled `demo response` in the
  UI. Set `AI_DEMO_MODE=auto` and add a funded `ANTHROPIC_API_KEY` in the host's
  environment variables to switch the real model on.

## Layout

```
app/
  main.py        FastAPI app, routers, dashboard route
  config.py      settings from .env
  database.py    engine, session, table creation + category seed
  models.py      Transaction, Category, Budget
  schemas.py     request/response shapes
  routers/       transactions, analytics, budgets, forecast, ai
  services/      analytics_service (math), forecast_service (projections),
                 ai_service (Claude), demo_ai (offline stand-in replies)
scripts/         seed_demo.py — a realistic history to look around with
static/          css, js, vendored Chart.js
tests/           test_ai.py, test_forecast.py, test_demo_ai.py
                 (all three run without an API key)
templates/       index.html
data/            finance.db (gitignored)
```

## Notes

- Money is stored as `Numeric(12, 2)` and travels as a JSON string, so no
  amount picks up binary rounding error in transit.
- Amounts are currency-neutral — the UI and the AI both treat them as plain
  numbers, so it works with whatever currency you actually use.
- Budgets warn at 80% of the monthly limit and flag `over` past 100%. The
  forecast judges the same budgets on where the month *ends* instead.
- Chat history lives in the browser; the API is stateless and takes prior turns
  on each request.

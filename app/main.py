"""FastAPI application entry point. Routers get wired up here."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db
from app.routers import ai, analytics, budgets, forecast, transactions

BASE_DIR = Path(__file__).resolve().parent.parent


def _seed_demo_if_empty() -> None:
    """Fill a brand-new database with the sample history.

    Hosting platforms wipe the container filesystem on every restart, so the
    demo data is rebuilt here rather than shipped in the image. Imports are
    local because scripts/ imports app.database, which imports this module.
    """
    import random
    from datetime import date
    from decimal import Decimal

    from app.database import SessionLocal
    from app.models import Budget, Transaction
    from scripts.seed_demo import BUDGETS, SEED, build_history

    with SessionLocal() as db:
        if db.query(Transaction).count():
            return  # somebody's real data — leave it alone
        db.add_all(build_history(date.today(), random.Random(SEED)))
        db.add_all(
            Budget(category=name, monthly_limit=Decimal(limit))
            for name, limit in BUDGETS.items()
        )
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create tables if the database file is new
    if settings.seed_demo_data:
        _seed_demo_if_empty()
    yield


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.include_router(transactions.router)
app.include_router(analytics.router)
app.include_router(budgets.router)
app.include_router(forecast.router)
app.include_router(ai.router)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serves the single-page dashboard."""
    return templates.TemplateResponse(
        request=request, name="index.html", context={"app_name": settings.app_name}
    )


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}

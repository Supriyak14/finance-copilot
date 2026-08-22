"""API endpoints for spending forecasts."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import Forecast
from app.services import forecast_service

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("", response_model=Forecast)
def forecast(
    db: Session = Depends(get_db),
    horizon: int = Query(
        default=forecast_service.DEFAULT_HORIZON, ge=1, le=12,
        description="How many months ahead to project",
    ),
    lookback: int = Query(
        default=forecast_service.DEFAULT_LOOKBACK, ge=1, le=36,
        description="How many complete months of history to learn from",
    ),
):
    """Where this month lands, what the next few cost, and which budgets break.

    Computed entirely in Python — no API key needed and the same history always
    gives the same answer.
    """
    return forecast_service.build_forecast(db, horizon=horizon, lookback=lookback)

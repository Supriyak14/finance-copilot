"""API endpoints that return summary numbers for the charts."""

from datetime import date as date_type

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TransactionType
from app.schemas import CategoryTotal, MonthlyPoint, Summary
from app.services import analytics_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary", response_model=Summary)
def summary(
    db: Session = Depends(get_db),
    start: date_type | None = None,
    end: date_type | None = None,
):
    """Headline numbers: income, expense, net, savings rate."""
    return analytics_service.get_summary(db, start=start, end=end)


@router.get("/by-category", response_model=list[CategoryTotal])
def by_category(
    db: Session = Depends(get_db),
    start: date_type | None = None,
    end: date_type | None = None,
    tx_type: TransactionType = Query(default=TransactionType.EXPENSE, alias="type"),
):
    """Category totals for the doughnut chart."""
    return analytics_service.get_spend_by_category(db, start=start, end=end, tx_type=tx_type)


@router.get("/monthly", response_model=list[MonthlyPoint])
def monthly(db: Session = Depends(get_db), months: int = Query(default=6, ge=1, le=36)):
    """Income vs expense per month for the bar chart."""
    return analytics_service.get_monthly_trend(db, months=months)

"""API endpoints for monthly category budgets and the alerts they produce."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Budget
from app.schemas import BudgetCreate, BudgetOut, BudgetStatus
from app.services import analytics_service

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetOut])
def list_budgets(db: Session = Depends(get_db)):
    return db.execute(select(Budget).order_by(Budget.category)).scalars().all()


@router.put("", response_model=BudgetOut, status_code=status.HTTP_200_OK)
def set_budget(payload: BudgetCreate, db: Session = Depends(get_db)):
    """Create or update the budget for a category.

    PUT rather than POST: a category has at most one budget, so re-submitting the
    form should move the limit, not fail on the unique constraint.
    """
    budget = db.execute(
        select(Budget).where(Budget.category == payload.category)
    ).scalar_one_or_none()

    if budget is None:
        budget = Budget(**payload.model_dump())
        db.add(budget)
    else:
        budget.monthly_limit = payload.monthly_limit

    db.commit()
    db.refresh(budget)
    return budget


@router.get("/status", response_model=list[BudgetStatus])
def budget_status(db: Session = Depends(get_db)):
    """Each budget vs this month's spending — drives the warning banners."""
    return analytics_service.get_budget_status(db)


@router.delete(
    "/{budget_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_budget(budget_id: int, db: Session = Depends(get_db)):
    budget = db.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No budget with id {budget_id}"
        )
    db.delete(budget)
    db.commit()

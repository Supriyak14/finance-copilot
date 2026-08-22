"""API endpoints for adding / listing / editing / deleting transactions."""

from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, Transaction, TransactionType
from app.schemas import (
    CategoryOut,
    TransactionCreate,
    TransactionOut,
    TransactionPage,
    TransactionUpdate,
)

router = APIRouter(prefix="/api", tags=["transactions"])


def _get_or_404(db: Session, transaction_id: int) -> Transaction:
    transaction = db.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No transaction with id {transaction_id}",
        )
    return transaction


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    db: Session = Depends(get_db),
    tx_type: TransactionType | None = Query(default=None, alias="type"),
    category: str | None = None,
    start: date_type | None = None,
    end: date_type | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Newest first, filtered and paged."""
    filters = []
    if tx_type is not None:
        filters.append(Transaction.type == tx_type)
    if category:
        filters.append(Transaction.category == category)
    if start is not None:
        filters.append(Transaction.date >= start)
    if end is not None:
        filters.append(Transaction.date <= end)

    total = db.execute(
        select(func.count(Transaction.id)).where(*filters)
    ).scalar_one()

    items = (
        db.execute(
            select(Transaction)
            .where(*filters)
            # id as tiebreaker: several rows usually share a date, and without it
            # paging can show the same row twice.
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    return TransactionPage(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED
)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    transaction = Transaction(**payload.model_dump())
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction


@router.get("/transactions/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, transaction_id)


@router.patch("/transactions/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: int, payload: TransactionUpdate, db: Session = Depends(get_db)
):
    transaction = _get_or_404(db, transaction_id)

    # exclude_unset: an omitted field means "leave it alone", not "set it to null".
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
        )

    for field, value in changes.items():
        setattr(transaction, field, value)

    db.commit()
    db.refresh(transaction)
    return transaction


# response_class=Response: without it FastAPI labels the empty 204 as JSON,
# which browsers report as a failed request.
@router.delete(
    "/transactions/{transaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, transaction_id))
    db.commit()


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(
    db: Session = Depends(get_db),
    tx_type: TransactionType | None = Query(default=None, alias="type"),
):
    """Fills the category dropdown on the add-transaction form."""
    filters = [Category.type == tx_type] if tx_type is not None else []
    return (
        db.execute(select(Category).where(*filters).order_by(Category.name))
        .scalars()
        .all()
    )

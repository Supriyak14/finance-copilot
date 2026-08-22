"""API endpoints for AI insights and chat."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category
from app.schemas import (
    CategorizeRequest,
    CategorizeResponse,
    ChatRequest,
    ChatResponse,
    ForecastNarrative,
    InsightsOut,
)
from app.services import ai_service, analytics_service, forecast_service

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
def ai_status():
    """Lets the frontend show the right empty state before anyone clicks.

    `demo_available` is the one the buttons care about: when it is true a click
    always produces an answer, whether or not the API can be reached.
    """
    return {
        "configured": ai_service.is_configured(),
        "model": ai_service.MODEL,
        "demo_available": ai_service.demo_available(),
        "demo_model": ai_service.DEMO_MODEL,
        "mode": ai_service._demo_mode(),
    }


@router.post("/insights", response_model=InsightsOut)
def insights(db: Session = Depends(get_db)):
    """Advice generated from the user's own numbers."""
    snapshot = analytics_service.build_financial_snapshot(db)

    try:
        text, model = ai_service.generate_insights(snapshot)
    except ai_service.AIUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return InsightsOut(
        insights=text,
        generated_from=analytics_service.get_summary(db),
        model=model,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    """Ask a money question; the model answers against the live snapshot."""
    snapshot = analytics_service.build_financial_snapshot(db)
    history = [m.model_dump() for m in payload.history]

    try:
        reply, model = ai_service.answer_question(snapshot, payload.message, history)
    except ai_service.AIUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return ChatResponse(reply=reply, model=model)


@router.post("/categorize", response_model=CategorizeResponse)
def categorize(payload: CategorizeRequest, db: Session = Depends(get_db)):
    """Suggest a category for a transaction note.

    The candidate list comes from the database, so the model can only ever pick
    a category this install actually has.
    """
    categories = (
        db.execute(
            select(Category.name).where(Category.type == payload.type).order_by(Category.name)
        )
        .scalars()
        .all()
    )

    try:
        result = ai_service.suggest_category(
            description=payload.description,
            categories=list(categories),
            tx_type=payload.type.value,
            amount=float(payload.amount) if payload.amount is not None else None,
        )
    except ai_service.AIUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return CategorizeResponse(**result)


@router.post("/forecast", response_model=ForecastNarrative)
def forecast_narrative(db: Session = Depends(get_db)):
    """Explain the forecast in plain English.

    The forecast itself is computed first, in Python, and handed over finished —
    the model only narrates numbers it cannot change.
    """
    snapshot = forecast_service.build_forecast_snapshot(db)

    try:
        text, model = ai_service.explain_forecast(snapshot)
    except ai_service.AIUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return ForecastNarrative(narrative=text, model=model)

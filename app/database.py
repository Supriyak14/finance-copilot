"""SQLite connection + session setup."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite files live under data/; make sure the directory exists before connecting.
if settings.database_url.startswith("sqlite"):
    (BASE_DIR / "data").mkdir(exist_ok=True)

# check_same_thread=False: FastAPI serves requests from a thread pool, and each
# session is still confined to a single request.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create any missing tables and seed the category picklist. Runs on startup."""
    from app import models  # (registers models on Base.metadata)

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        existing = {
            (name, type_)
            for name, type_ in db.query(models.Category.name, models.Category.type)
        }
        new = [
            models.Category(name=name, type=type_)
            for name, type_ in models.DEFAULT_CATEGORIES
            if (name, type_) not in existing
        ]
        if new:
            db.add_all(new)
            db.commit()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

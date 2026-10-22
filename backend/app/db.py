"""Sesja i silnik bazy danych."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

_url = settings.resolved_database_url()
# timeout: gdy inny proces pisze (np. trwajacy sync raportow), czekaj zamiast
# rzucac "database is locked". Na Postgresie nieistotne - to specyfika SQLite.
_connect_args = (
    {"check_same_thread": False, "timeout": 30.0} if _url.startswith("sqlite") else {}
)

engine = create_engine(_url, echo=False, future=True, connect_args=_connect_args)

if _url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - konfiguracja połączenia
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesja z automatycznym commit/rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Dependency dla FastAPI."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401  - rejestracja tabel w metadata

    models.Base.metadata.create_all(engine)

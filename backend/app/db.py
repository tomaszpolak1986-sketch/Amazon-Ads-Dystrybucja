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
    _dodaj_brakujace_kolumny(models.Base.metadata)


def _dodaj_brakujace_kolumny(metadata) -> None:
    """Dokłada do istniejących tabel kolumny, które doszły w modelu.

    `create_all` tworzy brakujące tabele, ale istniejącej tabeli nie zmienia -
    nowa kolumna w modelu (np. `campaigns.off_amazon_strategy` z 1.6.0)
    kończyłaby się błędem przy pierwszym zapytaniu. U klienta aktualizacja
    przychodzi w nocy bez udziału człowieka, więc schemat musi dogonić model
    sam, przy starcie.

    Wyłącznie ADD COLUMN dla kolumn dopuszczających NULL: nic nie kasuje, nie
    zmienia typów i nie rusza danych. Kolumnę NOT NULL bez wartości domyślnej
    pomijamy z ostrzeżeniem - takiej nie da się dodać do tabeli z wierszami.
    """
    import logging

    from sqlalchemy import inspect

    log = logging.getLogger(__name__)
    insp = inspect(engine)
    istniejace = set(insp.get_table_names())
    with engine.begin() as conn:
        for tabela in metadata.sorted_tables:
            if tabela.name not in istniejace:
                continue
            w_bazie = {c["name"] for c in insp.get_columns(tabela.name)}
            for kol in tabela.columns:
                if kol.name in w_bazie:
                    continue
                if not kol.nullable and kol.server_default is None:
                    log.warning("Kolumna %s.%s wymaga ręcznej migracji (NOT NULL)", tabela.name, kol.name)
                    continue
                typ = kol.type.compile(dialect=engine.dialect)
                conn.exec_driver_sql(f'ALTER TABLE "{tabela.name}" ADD COLUMN "{kol.name}" {typ}')
                log.info("Dodano kolumnę %s.%s (%s)", tabela.name, kol.name, typ)

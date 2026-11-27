"""Sesja i silnik bazy danych."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

_url = settings.resolved_database_url()
# timeout: gdy inny proces pisze (np. trwajacy sync raportow), czekaj zamiast
# rzucac "database is locked". Na Postgresie nieistotne - to specyfika SQLite.
#
# Postgres z pudelka ma ustawienia dla malej maszyny sprzed dwudziestu lat:
# 4 MB na sortowanie (kazde zestawienie za 30 dni szlo przez pliki tymczasowe;
# hash_mem_multiplier pozwala sumowac milion wierszy w pamieci, a gdy jej
# zabraknie, Postgres i tak zrzuca nadmiar na dysk zamiast padac)
# i kompilacje JIT, ktora przy naszych zapytaniach kosztuje wiecej, niz daje.
# Podajemy je PRZY POLACZENIU, nie w docker-compose - u klienta noca podmienia
# sie tylko obraz, pliku compose aktualizacja nie rusza.
# Parallel hash wylaczony swiadomie: rezerwuje pamiec w /dev/shm, ktorego
# kontener ma domyslnie 64 MB, i przy wiekszym work_mem konczylby sie bledem
# "could not resize shared memory segment".
_PG_OPCJE = (
    "-c work_mem=32MB -c hash_mem_multiplier=3 -c jit=off "
    "-c enable_parallel_hash=off"
)
_connect_args = (
    {"check_same_thread": False, "timeout": 30.0}
    if _url.startswith("sqlite")
    else {"options": _PG_OPCJE}
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


# Indeksy dodane po wdrozeniu: (tabela, nazwa, kolumny). `create_all` nie
# dodaje indeksow do istniejacych tabel, wiec u klienta trzeba je zbudowac.
_NOWE_INDEKSY = (
    ("metrics_product_daily", "ix_product_daily_profil_data", "profile_id, date"),
    ("metrics_search_term_daily", "ix_search_term_daily_profil_data", "profile_id, date"),
    ("metrics_target_daily", "ix_target_daily_profil_data", "profile_id, date"),
)
# Zastapione przez powyzsze albo przez poczatek indeksu unikalnego.
_ZBEDNE_INDEKSY = (
    "ix_metrics_product_daily_date",
    "ix_metrics_product_daily_profile_id",
    "ix_metrics_product_daily_ad_product",
    "ix_metrics_product_daily_ad_group_id",
    "ix_metrics_search_term_daily_profile_id",
    "ix_metrics_target_daily_profile_id",
)
_TABELE_FAKTOW = (
    "metrics_campaign_daily", "metrics_placement_daily", "metrics_target_daily",
    "metrics_search_term_daily", "metrics_product_daily", "total_sales_daily",
)


def sprzatanie_jednorazowe() -> None:
    """Jednorazowe porzadki w danych - kazdy krok raz, ze znacznikiem w data/.

    * puste wiersze produktow SB/SD (same zera) - od wersji 1.7.2 nie sa
      zapisywane, a do tej pory stanowily 78% wierszy SD w najwiekszej
      tabeli bazy. Suma zer to to samo co brak wiersza, wiec wyniki sie nie
      zmieniaja.
    """
    import logging

    from .config import PROJECT_ROOT

    log = logging.getLogger(__name__)
    znacznik = PROJECT_ROOT / "data" / "sprzatanie-puste-sd.ok"
    if engine.dialect.name != "postgresql" or znacznik.exists():
        return
    try:
        with engine.begin() as conn:
            ile = conn.execute(text(
                "DELETE FROM metrics_product_daily WHERE ad_product IN ('SB', 'SD') "
                "AND impressions = 0 AND clicks = 0 AND cost = 0 AND orders = 0 "
                "AND sales = 0 AND units = 0 AND coalesce(sales_other_sku, 0) = 0 "
                "AND coalesce(units_other_sku, 0) = 0"
            )).rowcount
        log.info("Sprzatanie: usunieto %s pustych wierszy produktow SB/SD", ile)
        znacznik.parent.mkdir(parents=True, exist_ok=True)
        znacznik.write_text("ok", encoding="utf-8")
    except Exception:  # noqa: BLE001 - sprobuje przy nastepnym starcie
        log.warning("Sprzatanie pustych wierszy SB/SD nie powiodlo sie", exc_info=True)


def indeksy_wydajnosci() -> None:
    """Dostosowuje indeksy istniejacej bazy - w tle, bez blokowania tabel.

    CONCURRENTLY, bo na tabeli z milionami wierszy zwykly CREATE INDEX
    zablokowalby zapis na minute albo dwie, a startu panelu nie wolno tak
    dlugo wstrzymywac: nocna aktualizacja czeka na niego 3 minuty i potem
    cofa wersje. Indeks przerwany w polowie (restart) zostaje w bazie jako
    nieprawidlowy - takiego nie omijamy, tylko budujemy od nowa.
    """
    import logging

    log = logging.getLogger(__name__)
    if engine.dialect.name != "postgresql":
        return
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for tabela, nazwa, kolumny in _NOWE_INDEKSY:
                stan = conn.exec_driver_sql(
                    "SELECT i.indisvalid FROM pg_class c JOIN pg_index i "
                    "ON i.indexrelid = c.oid WHERE c.relname = %(n)s", {"n": nazwa},
                ).scalar()
                if stan is True:
                    continue
                if stan is False:
                    conn.exec_driver_sql(f'DROP INDEX CONCURRENTLY IF EXISTS "{nazwa}"')
                log.info("Buduje indeks %s (jednorazowo, w tle)...", nazwa)
                conn.exec_driver_sql(
                    f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{nazwa}" ON "{tabela}" ({kolumny})'
                )
                conn.exec_driver_sql(f'ANALYZE "{tabela}"')
                log.info("Indeks %s gotowy", nazwa)
            for nazwa in _ZBEDNE_INDEKSY:
                conn.exec_driver_sql(f'DROP INDEX CONCURRENTLY IF EXISTS "{nazwa}"')
            # Pobieranie kasuje i wstawia te same dni co kilka godzin. Domyslny
            # prog autovacuum (20% tabeli) przy milionach wierszy oznacza
            # setki tysiecy martwych wierszy, przez ktore przedziera sie kazde
            # zestawienie - sprzatamy czesciej i czesciej odswiezamy statystyki.
            for tabela in _TABELE_FAKTOW:
                conn.exec_driver_sql(
                    f'ALTER TABLE "{tabela}" SET (autovacuum_vacuum_scale_factor = 0.05, '
                    "autovacuum_analyze_scale_factor = 0.02)"
                )
    except Exception:  # noqa: BLE001 - panel dziala i bez tego, tylko wolniej
        log.exception("Nie udalo sie dostosowac indeksow - sprobuje przy nastepnym starcie")


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

"""Przenosi zawartosc lokalnej bazy SQLite do Postgresa na serwerze.

Pelne pobranie 95 dni dla szesciu rynkow trwa okolo czterech godzin, wiec
przeniesienie gotowych danych jest znacznie tansze niz sciaganie ich od nowa.

Skrypt czyta ZRODLO przez osobny silnik, a zapisuje przez silnik aplikacji -
dzieki temu docelowa baza bierze sie z DATABASE_URL i nie da sie pomylic
kierunku. Tabele docelowe musza byc puste albo zostana wyczyszczone (--reset),
zeby nie robic duplikatow.

Uzycie (na serwerze, po skopiowaniu pliku .db):
    DATABASE_URL=postgresql+psycopg://... \
      python backend/scripts/migrate_to_postgres.py --source /tmp/adspanel.db

    ... --dry-run    tylko policz wiersze, nie zapisuj
    ... --reset      wyczysc tabele docelowe przed zapisem
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, delete, func, insert, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import engine as target_engine, init_db  # noqa: E402
from app.models import Base  # noqa: E402

log = logging.getLogger("migrate")

# Partie, zeby nie trzymac calej tabeli faktow w pamieci - metrics_target_daily
# potrafi miec kilkadziesiat tysiecy wierszy na rynek.
CHUNK = 5_000


def table_order() -> list:
    """Tabele w kolejnosci zaleznosci - rodzice przed dziecmi."""
    return Base.metadata.sorted_tables


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="sciezka do pliku .db (SQLite)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true", help="wyczysc tabele docelowe")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    )

    src_path = Path(args.source).resolve()
    if not src_path.exists():
        log.error("Nie ma pliku zrodlowego: %s", src_path)
        return 1

    target_url = str(target_engine.url)
    if target_url.startswith("sqlite"):
        log.error(
            "DATABASE_URL wskazuje na SQLite (%s). Ten skrypt ma przenosic DO "
            "Postgresa - ustaw DATABASE_URL na baze docelowa.", target_url,
        )
        return 1

    source_engine = create_engine(f"sqlite:///{src_path.as_posix()}", future=True)
    log.info("Zrodlo: %s", src_path)
    log.info("Cel:    %s", target_engine.url.render_as_string(hide_password=True))

    init_db()
    tables = table_order()

    with Session(source_engine) as src, Session(target_engine) as dst:
        plan: list[tuple] = []
        for table in tables:
            n_src = src.execute(select(func.count()).select_from(table)).scalar() or 0
            n_dst = dst.execute(select(func.count()).select_from(table)).scalar() or 0
            plan.append((table, n_src, n_dst))
            znak = "" if not n_dst else "  <-- cel niepusty"
            log.info("%-28s zrodlo %8s | cel %8s%s", table.name, n_src, n_dst, znak)

        zajete = [t for t, _, n in plan if n]
        if zajete and not args.reset:
            log.error(
                "Tabele docelowe nie sa puste: %s. Uruchom z --reset, jesli chcesz "
                "je nadpisac, albo wyczysc je recznie.",
                ", ".join(t.name for t in zajete),
            )
            return 1

        if args.dry_run:
            log.info("--dry-run: nic nie zapisano.")
            return 0

        if args.reset:
            # Odwrotna kolejnosc: dzieci przed rodzicami, inaczej klucze obce
            for table in reversed(tables):
                dst.execute(delete(table))
            dst.commit()
            log.info("Tabele docelowe wyczyszczone.")

        przeniesione = 0
        for table, n_src, _ in plan:
            if not n_src:
                continue
            offset = 0
            while True:
                rows = src.execute(
                    select(table).limit(CHUNK).offset(offset)
                ).mappings().all()
                if not rows:
                    break
                dst.execute(insert(table), [dict(r) for r in rows])
                dst.commit()
                offset += len(rows)
                if offset % (CHUNK * 5) == 0:
                    log.info("  %s: %s/%s", table.name, offset, n_src)
            przeniesione += offset
            log.info("%-28s przeniesiono %s", table.name, offset)

        log.info("Razem wierszy: %s", przeniesione)

        # Kontrola: liczby po obu stronach musza sie zgadzac
        rozjazd = []
        for table, n_src, _ in plan:
            n_dst = dst.execute(select(func.count()).select_from(table)).scalar() or 0
            if n_dst != n_src:
                rozjazd.append(f"{table.name}: zrodlo {n_src}, cel {n_dst}")
        if rozjazd:
            log.error("ROZJAZD PO MIGRACJI: %s", "; ".join(rozjazd))
            return 1

    log.info("Migracja zakonczona - liczby wierszy zgadzaja sie w kazdej tabeli.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

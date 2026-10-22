"""Dodaje kolumne tos_impression_share do tabel faktow.

Panel nie ma jeszcze systemu migracji - baza powstaje z create_all, ktore nie
rusza istniejacych tabel. Ta jednorazowa migracja dokłada brakujaca kolumne,
zeby nie trzeba bylo kasowac danych. Kolumna jest opcjonalna, wiec stare
wiersze zostaja z NULL - i tak jest to prawda: dla nich udzialu nie pobralismy.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import engine  # noqa: E402

TABLES = [
    "metrics_campaign_daily", "metrics_placement_daily", "metrics_target_daily",
    "metrics_search_term_daily", "metrics_product_daily",
]
COLUMN = "tos_impression_share"


def main() -> int:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    added, skipped = [], []

    with engine.begin() as conn:
        for table in TABLES:
            if table not in existing:
                skipped.append(f"{table} (brak tabeli)")
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if COLUMN in cols:
                skipped.append(f"{table} (juz jest)")
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {COLUMN} FLOAT"))
            added.append(table)

    print(f"Dodano kolumne w: {', '.join(added) if added else '-'}")
    print(f"Pominieto: {', '.join(skipped) if skipped else '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

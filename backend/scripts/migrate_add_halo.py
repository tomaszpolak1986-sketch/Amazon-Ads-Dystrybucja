"""Dodaje kolumny efektu halo do tabel faktow.

Amazon oddaje sprzedaz innych produktow (salesOtherSku7d) tylko w raportach
targetowania, fraz i produktow. Kolumny sa opcjonalne: przy kampaniach
i placementach zostaja puste i tak ma byc - brak danych to nie brak efektu.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import engine  # noqa: E402

TABELE = [
    "metrics_campaign_daily", "metrics_placement_daily", "metrics_target_daily",
    "metrics_search_term_daily", "metrics_product_daily",
]
KOLUMNY = {"sales_other_sku": "FLOAT", "units_other_sku": "INTEGER"}


def main() -> int:
    inspector = inspect(engine)
    istniejace = set(inspector.get_table_names())
    dodane, pominiete = [], []

    with engine.begin() as conn:
        for tabela in TABELE:
            if tabela not in istniejace:
                pominiete.append(f"{tabela} (brak tabeli)")
                continue
            kolumny = {c["name"] for c in inspector.get_columns(tabela)}
            for nazwa, typ in KOLUMNY.items():
                if nazwa in kolumny:
                    pominiete.append(f"{tabela}.{nazwa} (juz jest)")
                    continue
                conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {nazwa} {typ}"))
                dodane.append(f"{tabela}.{nazwa}")

    print(f"Dodano: {len(dodane)}")
    for d in dodane:
        print(f"  {d}")
    if pominiete:
        print(f"Pominieto: {len(pominiete)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

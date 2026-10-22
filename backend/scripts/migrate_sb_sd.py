"""Dostosowuje schemat do obsługi Sponsored Brands i Sponsored Display.

Do tej pory panel znał wyłącznie Sponsored Products, więc tabele nie musiały
rozróżniać typu kampanii. Po dołożeniu SB i SD to rozróżnienie jest
**warunkiem bezpieczeństwa**, nie wygodą: bez kolumny `ad_product` synchronizacja
jednego typu archiwizowała albo kasowała dane pozostałych.

Skrypt jest idempotentny — można go puścić wielokrotnie.

Dwie rzeczy, na które trzeba uważać przy Postgresie:

* Kolumna jest `NOT NULL`, a tabele mają dane. Dodajemy ją więc **z wartością
  domyślną po stronie SQL**; sam `NOT NULL` bez `DEFAULT` przewróciłby się na
  istniejących wierszach.
* Wszystko, co już w tabelach leży, pochodzi ze Sponsored Products — dlatego
  domyślna wartość to `'SP'` i po migracji nie trzeba niczego przepisywać.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import Base, Listing  # noqa: E402

# Tabele, które muszą odróżniać typ produktu reklamowego.
TABELE_Z_TYPEM = [
    "product_ads",
    "metrics_target_daily",
    "metrics_search_term_daily",
    "metrics_product_daily",
]

# Indeks złączenia product_ads z metrykami. Zmierzone: 5,2 s -> 1,7 s.
INDEKS = (
    "ix_product_daily_join",
    "metrics_product_daily",
    "(ad_group_id, sku, date)",
)


def main() -> int:
    inspector = inspect(engine)
    istniejace = set(inspector.get_table_names())
    zrobione: list[str] = []
    pominiete: list[str] = []

    # --- tabela ofert (pelna baza produktowa z SP-API) ---
    if "listings" not in istniejace:
        Listing.__table__.create(engine)
        zrobione.append("tabela listings")
    else:
        kolumny = {c["name"] for c in inspector.get_columns("listings")}
        if "asin_inferred" not in kolumny:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE listings ADD COLUMN asin_inferred BOOLEAN "
                    "DEFAULT FALSE NOT NULL"
                ))
            zrobione.append("listings.asin_inferred")
        else:
            pominiete.append("listings")

    # --- kolumna typu produktu reklamowego ---
    for tabela in TABELE_Z_TYPEM:
        if tabela not in istniejace:
            pominiete.append(f"{tabela} (brak tabeli)")
            continue
        kolumny = {c["name"] for c in inspector.get_columns(tabela)}
        if "ad_product" in kolumny:
            pominiete.append(f"{tabela}.ad_product")
            continue
        with engine.begin() as conn:
            # DEFAULT po stronie SQL jest tu konieczny - inaczej NOT NULL
            # przewroci sie na istniejacych wierszach.
            conn.execute(text(
                f"ALTER TABLE {tabela} ADD COLUMN ad_product VARCHAR(24) "
                "DEFAULT 'SP' NOT NULL"
            ))
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{tabela}_ad_product "
                f"ON {tabela} (ad_product)"
            ))
        zrobione.append(f"{tabela}.ad_product")

    # --- indeks zlaczenia ---
    nazwa, tabela, kolumny_idx = INDEKS
    if tabela in istniejace:
        indeksy = {i["name"] for i in inspector.get_indexes(tabela)}
        if nazwa in indeksy:
            pominiete.append(nazwa)
        else:
            with engine.begin() as conn:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {nazwa} ON {tabela} {kolumny_idx}"
                ))
            zrobione.append(nazwa)

    # --- reszta schematu, gdyby czegos brakowalo ---
    Base.metadata.create_all(engine)

    print("Zmienione:", ", ".join(zrobione) or "nic")
    print("Pominiete (juz byly):", ", ".join(pominiete) or "nic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

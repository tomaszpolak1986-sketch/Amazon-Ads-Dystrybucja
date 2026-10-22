"""Zasypuje dziury w danych raportowych wykryte przez przeglad pokrycia.

Pobiera wylacznie brakujace typy raportow na tych rynkach, ktore ich nie maja -
nie powtarza calego pobrania. Rynek po rynku, zeby nie zderzyc sie z limitem
zapytan; w obrebie rynku raporty i tak leca rownolegle.

Uzycie:
    python backend/scripts/backfill_gaps.py --days 95
    python backend/scripts/backfill_gaps.py --days 95 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.etl import coverage, sync  # noqa: E402

log = logging.getLogger("backfill_gaps")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=95)
    ap.add_argument("--dry-run", action="store_true", help="tylko pokaz, co by pobral")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    init_db()

    start, end, markets = coverage.survey(args.days)
    do_zrobienia = [(m, m.gaps) for m in markets if m.gaps]

    if not do_zrobienia:
        log.info("Brak dziur w zakresie %s..%s - nie ma czego dociagac.", start, end)
        return 0

    for m, gaps in do_zrobienia:
        log.info(
            "%s: brakuje %s",
            m.country,
            ", ".join(f"{c.label} ({c.days}/{c.expected} dni)" for c in gaps),
        )
    if args.dry_run:
        return 0

    summary: dict[str, dict] = {}
    for i, (m, gaps) in enumerate(do_zrobienia, 1):
        kinds = [c.kind for c in gaps]
        log.info("[%s/%s] %s - start (%s)", i, len(do_zrobienia), m.country, ", ".join(kinds))
        began = time.time()
        try:
            summary[m.country] = sync.sync_reports(m.profile_id, start, end, kinds=kinds)
        except Exception as exc:
            log.exception("[%s] nie powiodl sie", m.country)
            summary[m.country] = {"error": str(exc)[:200]}
        log.info(
            "[%s/%s] %s - koniec po %.0f min: %s",
            i, len(do_zrobienia), m.country, (time.time() - began) / 60, summary[m.country],
        )

    log.info("PODSUMOWANIE DZIUR: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

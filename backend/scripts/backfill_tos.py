"""Uzupelnia udzial wyswietlen na gorze wynikow tam, gdzie go brakuje.

Kolumna `tos_impression_share` powstala pozniej niz czesc danych, a procesy
pobierajace trzymaja w pamieci kod z chwili startu - rynki sciagniete wczesniej
maja wiersze bez tej metryki. Zamiast powtarzac cale pobranie, dociagamy tylko
te trzy typy raportow, ktore ja niosa.

Uzycie:
    python backend/scripts/backfill_tos.py --days 95
    python backend/scripts/backfill_tos.py --days 95 --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.etl import sync  # noqa: E402
from app.models import CampaignDaily, Profile  # noqa: E402

log = logging.getLogger("backfill_tos")

# Tylko te raporty niosa topOfSearchImpressionShare - reszty nie ruszamy.
KINDS = ["campaign", "placement", "targeting"]


def markets_missing_share() -> list[tuple[int, str, int]]:
    """Rynki, ktore maja dane kampanii, ale zaden wiersz z udzialem."""
    out = []
    with session_scope() as session:
        for pid, cc in session.execute(
            select(Profile.profile_id, Profile.country_code).order_by(Profile.country_code)
        ).all():
            total = session.execute(
                select(func.count()).select_from(CampaignDaily)
                .where(CampaignDaily.profile_id == pid)
            ).scalar() or 0
            if not total:
                continue
            with_share = session.execute(
                select(func.count()).select_from(CampaignDaily).where(
                    CampaignDaily.profile_id == pid,
                    CampaignDaily.tos_impression_share.isnot(None),
                )
            ).scalar() or 0
            if with_share == 0:
                out.append((pid, cc or str(pid), total))
    return out


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

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)

    targets = markets_missing_share()
    if not targets:
        log.info("Kazdy rynek z danymi ma juz udzial wyswietlen - nie ma czego dociagac.")
        return 0

    log.info(
        "Rynki bez udzialu wyswietlen: %s (%s..%s, typy: %s)",
        ", ".join(f"{cc} ({n} wierszy)" for _, cc, n in targets),
        start, end, ", ".join(KINDS),
    )
    if args.dry_run:
        return 0

    summary: dict[str, dict] = {}
    for i, (pid, country, _) in enumerate(targets, 1):
        log.info("[%s/%s] %s - start", i, len(targets), country)
        began = time.time()
        try:
            summary[country] = sync.sync_reports(pid, start, end, kinds=KINDS)
        except Exception as exc:
            log.exception("[%s] nie powiodl sie", country)
            summary[country] = {"error": str(exc)[:200]}
        log.info(
            "[%s/%s] %s - koniec po %.0f min: %s",
            i, len(targets), country, (time.time() - began) / 60, summary[country],
        )

    log.info("PODSUMOWANIE UDZIALU: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

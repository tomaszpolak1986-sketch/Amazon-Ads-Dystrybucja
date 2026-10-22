"""Pobranie raportow dla wszystkich aktywnych rynkow, jeden po drugim.

Rynki bez kampanii pomijamy - nie ma czego raportowac. Idziemy sekwencyjnie,
zeby nie zderzyc sie z limitem zapytan Amazona: w obrebie jednego rynku i tak
pobieramy szesc raportow rownolegle, wiec przepustowosc jest wykorzystana.

Uzycie:
    python backend/scripts/sync_all_markets.py --days 95
    python backend/scripts/sync_all_markets.py --days 30 --skip DE
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.etl import sync  # noqa: E402
from app.models import Campaign, Profile  # noqa: E402

log = logging.getLogger("sync_all")


def active_markets() -> list[tuple[int, str]]:
    """Rynki, na ktorych sa niezarchiwizowane kampanie."""
    with session_scope() as session:
        rows = session.execute(
            select(Profile.profile_id, Profile.country_code, func.count(Campaign.campaign_id))
            .join(Campaign, Campaign.profile_id == Profile.profile_id, isouter=True)
            .group_by(Profile.profile_id, Profile.country_code)
            .order_by(func.count(Campaign.campaign_id).desc())
        ).all()
    return [(pid, cc or str(pid)) for pid, cc, n in rows if n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=95)
    ap.add_argument("--skip", action="append", default=[], help="kod kraju do pominiecia")
    ap.add_argument(
        "--only", default=None,
        help="tylko te rynki, po przecinku (np. FR,IT,ES) - reszta pominieta",
    )
    ap.add_argument(
        "--kinds", default=None,
        help="tylko te typy raportow, po przecinku (np. product)",
    )
    ap.add_argument(
        "--parallel", type=int, default=2,
        help="ile rynkow naraz (1 = po kolei). Amazon generuje raport ~20 min, "
             "wiec sekwencyjnie szesc rynkow to trzy godziny czekania.",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    init_db()

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)
    # Do wykluczen z linii polecen dokladamy stale, z ustawien - rynki, na
    # ktorych konto ma profil, ale nie prowadzi sprzedazy. Bez tego kazdy
    # przebieg czekalby na raporty, ktore i tak przyjda puste.
    from app.config import settings
    skip = {s.upper() for s in args.skip} | settings.skip_market_list
    only = {c.strip().upper() for c in args.only.split(",")} if args.only else None
    kinds = [k.strip() for k in args.kinds.split(",")] if args.kinds else None

    markets = [
        (p, c) for p, c in active_markets()
        if c.upper() not in skip and (only is None or c.upper() in only)
    ]
    if not markets:
        log.error("Zaden rynek nie pasuje do filtrow (--only=%s --skip=%s)", args.only, skip)
        return 1
    log.info(
        "Rynki do pobrania: %s (%s..%s)",
        ", ".join(c for _, c in markets), start, end,
    )

    summary: dict[str, dict] = {}

    def jeden(idx: int, pid: int, country: str) -> None:
        log.info("[%s/%s] %s - start", idx, len(markets), country)
        began = time.time()
        try:
            wynik = sync.sync_reports(pid, start, end, kinds=kinds)
        except Exception as exc:
            log.exception("[%s] nie powiodl sie", country)
            wynik = {"error": str(exc)[:200]}
        summary[country] = wynik
        log.info(
            "[%s/%s] %s - koniec po %.0f min: %s",
            idx, len(markets), country, (time.time() - began) / 60, wynik,
        )

    rownolegle = max(1, args.parallel)
    if rownolegle == 1:
        for i, (pid, country) in enumerate(markets, 1):
            jeden(i, pid, country)
    else:
        # Czas idzie prawie w calosci na czekanie, az Amazon wygeneruje raport,
        # wiec kilka rynkow naraz nie obciaza nas - obciaza jego kolejke.
        # Trzymamy niska rownoleglosc, bo odpytywanie o gotowosc podlega limitom.
        log.info("Rynki pobierane po %s naraz", rownolegle)
        with ThreadPoolExecutor(max_workers=rownolegle) as pool:
            zadania = [
                pool.submit(jeden, i, pid, country)
                for i, (pid, country) in enumerate(markets, 1)
            ]
            for z in as_completed(zadania):
                z.result()

    log.info("PODSUMOWANIE: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

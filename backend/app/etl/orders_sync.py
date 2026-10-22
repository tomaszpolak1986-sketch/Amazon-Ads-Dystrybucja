"""Pobranie podzialu sprzedazy na FBA i wysylke wlasna.

Inaczej niz `sales_sync`, ktory chodzi rynek po rynku: tutaj pobieramy
**jeden raport na cale konto**. Raport zamowien zwraca zamowienia ze
wszystkich rynkow naraz - zmierzone: zapytanie o marketplace DE oddalo
Amazon.de, .fr, .it, .es, .co.uk i .nl. Pobieranie per rynek dawaloby te same
zamowienia dziesieciokrotnie i tyle samo razy obciazalo limit SP-API, ktory
przy dziesieciu rynkach i tak potrafi odmowic.

Rynek ustalamy z kolumny `sales-channel`, nie z tego, o co poprosilismy.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import delete, select

from ..config import settings
from ..db import session_scope
from .zapis import wstaw_lub_zastap
from ..models import FulfillmentDaily, Profile
from ..sp import orders_report, sales_report
from ..sp.client import SpApiError, SpClient

log = logging.getLogger(__name__)


def _profile_po_kraju() -> dict[str, int]:
    with session_scope() as session:
        rows = session.execute(
            select(Profile.country_code, Profile.profile_id)
        ).all()
    pomijane = settings.skip_market_list
    return {
        (k or "").upper(): p for k, p in rows
        if k and (k or "").upper() not in pomijane
    }


def _marketplace_ids() -> list[str]:
    with session_scope() as session:
        rows = session.execute(
            select(Profile.marketplace_id, Profile.country_code)
        ).all()
    pomijane = settings.skip_market_list
    return sorted({m for m, k in rows if m and (k or "").upper() not in pomijane})


def sync_window(start: dt.date, end: dt.date) -> dict[str, Any]:
    """Pobiera raport zamowien za okres i zapisuje sumy per dzien/rynek/kanal."""
    rynki = _marketplace_ids()
    if not rynki:
        return {"error": "Zaden profil nie ma przypisanego marketplace_id"}

    po_kraju = _profile_po_kraju()

    log.info("Podzial FBA/wlasna: raport zamowien %s..%s", start, end)
    try:
        with SpClient() as client:
            rid = orders_report.utworz(client, rynki, start, end)
            log.info("Raport zamowien zamowiony (%s)", rid)
            doc = sales_report._wait(client, rid)
            tekst = orders_report.pobierz_tekst(client, doc)
    except SpApiError as exc:
        log.warning("Raport zamowien nie powiodl sie: %s", str(exc)[:200])
        return {"error": str(exc)[:200]}

    wiersze = orders_report.agreguj(tekst)
    if not wiersze:
        return {"stored": 0, "note": "raport pusty"}

    do_zapisu = []
    bez_profilu: set[str] = set()
    for w in wiersze:
        pid = po_kraju.get(w["country"])
        if not pid:
            # Rynek, ktorego nie mamy w profilach reklamowych - sprzedaz tam
            # istnieje, ale nie ma jej do czego przypiac w panelu.
            bez_profilu.add(w["country"])
            continue
        do_zapisu.append({
            "date": w["date"], "profile_id": pid, "channel": w["channel"],
            "sales": w["sales"], "units": w["units"], "orders": w["orders"],
            "currency": w["currency"],
        })

    # Nadpisujemy caly zakres zamiast doklejac - Amazon koryguje zamowienia
    # wstecz (anulowania, zwroty), wiec ponowne pobranie ma zastapic poprzednie,
    # a nie zsumowac sie z nim.
    dni = sorted({w["date"] for w in do_zapisu})
    with session_scope() as session:
        if dni:
            session.execute(
                delete(FulfillmentDaily).where(FulfillmentDaily.date.in_(dni))
            )
            wstaw_lub_zastap(session, FulfillmentDaily, do_zapisu)

    wynik: dict[str, Any] = {"stored": len(do_zapisu), "days": len(dni)}
    if bez_profilu:
        wynik["markets_without_profile"] = sorted(bez_profilu)
    log.info("Podzial FBA/wlasna: %s", wynik)
    return wynik

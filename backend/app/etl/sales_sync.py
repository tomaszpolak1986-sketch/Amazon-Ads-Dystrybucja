"""Pobranie sprzedazy calkowitej z SP-API do tabeli TACOS.

Rynek po rynku, w oknach dat. Zapis idzie przez ten sam `total_sales.store`,
z ktorego korzysta import CSV - dzieki temu obie drogi sa wymienne i mozna
przelaczac sie miedzy nimi bez migracji danych.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import Profile
from ..sp.client import SpApiError, SpClient
from ..sp import sales_report
from . import total_sales

log = logging.getLogger(__name__)


def markets(profile_id: int | None = None) -> list[tuple[int, str, str]]:
    """(profile_id, kod kraju, marketplace_id) dla rynkow, ktore je maja."""
    with session_scope() as session:
        stmt = select(Profile.profile_id, Profile.country_code, Profile.marketplace_id)
        if profile_id:
            stmt = stmt.where(Profile.profile_id == profile_id)
        rows = session.execute(stmt.order_by(Profile.country_code)).all()
    # Rynki, na ktorych nie sprzedajemy, pomijamy u zrodla. Kazdy z nich to
    # osobny raport, czyli minuty przebiegu i zuzyty limit za same zera.
    pomijane = settings.skip_market_list
    return [
        (p, c or str(p), m) for p, c, m in rows
        if m and (c or "").upper() not in pomijane
    ]


def sync_market(
    client: SpClient, profile_id: int, marketplace_id: str,
    start: dt.date, end: dt.date,
) -> dict[str, Any]:
    """Pobiera i zapisuje sprzedaz calkowita jednego rynku."""
    zapisane = 0
    okna = 0
    bledy: list[str] = []
    per_asin = 0

    for ws, we in sales_report.date_windows(start, end):
        okna += 1
        try:
            rows = sales_report.fetch_window(client, marketplace_id, profile_id, ws, we)
        except SpApiError as exc:
            bledy.append(f"{ws}..{we}: {str(exc)[:160]}")
            continue

        if not rows:
            continue

        per_asin += sum(1 for r in rows if r["asin"])
        with session_scope() as session:
            wynik = total_sales.store(session, rows)
        zapisane += wynik["stored"]

    return {
        "stored": zapisane,
        "windows": okna,
        "asin_rows": per_asin,
        "errors": bledy,
    }


# Przerwa miedzy rynkami. SP-API odnawia prawo do zamowienia raportu mniej
# wiecej raz na minute, wiec dziesiec rynkow pod rzad wyczerpuje zapas - zawsze
# na tym samym, siodmym z kolei. Odstep nie usuwa problemu, ale zdejmuje czesc
# napiecia z limitu.
PRZERWA_MIEDZY_RYNKAMI = 10

# Ile czekac przed ponowieniem rynkow, ktore odbily sie od limitu. Zmierzone
# dwa dni z rzedu: po okolo siedmiu minutach zapas wraca i ponowienie
# przechodzi za pierwszym podejsciem.
PRZERWA_PRZED_PONOWIENIEM = 420


def _limit_przekroczony(wynik: dict[str, Any]) -> bool:
    """Czy rynek zawiodl przez limit zapytan, a nie przez brak dostepu.

    Rozroznienie jest istotne: 429 mija samo i warto ponowic, natomiast 403
    (brak roli Brand Analytics) bedzie sie powtarzac w nieskonczonosc, a
    ponawianie tylko przedluzyloby przebieg o kolejne siedem minut.
    """
    tresc = " ".join([str(wynik.get("error", "")), *map(str, wynik.get("errors", []))])
    return "429" in tresc or "QuotaExceeded" in tresc


def sync_all(
    start: dt.date, end: dt.date, profile_id: int | None = None
) -> dict[str, Any]:
    """Sprzedaz calkowita dla wszystkich rynkow, jeden po drugim.

    Rynki, ktore odbily sie od limitu SP-API, dostaja druga szanse po przerwie.
    Bez tego ostatnie rynki w kolejce - u nas zawsze PL - wypadaly z kazdego
    automatycznego przebiegu i trzeba je bylo dociagac recznie.
    """
    lista = markets(profile_id)
    if not lista:
        return {"error": "Zaden profil nie ma przypisanego marketplace_id"}

    log.info(
        "Sprzedaz calkowita: %s (%s..%s)",
        ", ".join(c for _, c, _ in lista), start, end,
    )

    podsumowanie: dict[str, Any] = {}
    do_ponowienia: list[tuple[int, str, str]] = []

    with SpClient() as client:
        for i, (pid, kraj, mp) in enumerate(lista, 1):
            if i > 1:
                time.sleep(PRZERWA_MIEDZY_RYNKAMI)
            log.info("[%s/%s] %s - start", i, len(lista), kraj)
            try:
                podsumowanie[kraj] = sync_market(client, pid, mp, start, end)
            except SpApiError as exc:
                # 403 na jednym rynku zwykle znaczy brak roli - reszta i tak
                # zawiedzie, ale nie przerywamy: raport z kazdego rynku osobno
                # jest bardziej uzyteczny niz jeden komunikat.
                podsumowanie[kraj] = {"error": str(exc)[:200]}
            log.info("[%s/%s] %s - %s", i, len(lista), kraj, podsumowanie[kraj])
            if _limit_przekroczony(podsumowanie[kraj]):
                do_ponowienia.append((pid, kraj, mp))

        if do_ponowienia:
            log.info(
                "Limit SP-API zablokowal %s rynkow (%s) - ponawiam za %s s",
                len(do_ponowienia), ", ".join(k for _, k, _ in do_ponowienia),
                PRZERWA_PRZED_PONOWIENIEM,
            )
            time.sleep(PRZERWA_PRZED_PONOWIENIEM)
            for pid, kraj, mp in do_ponowienia:
                try:
                    wynik = sync_market(client, pid, mp, start, end)
                except SpApiError as exc:
                    wynik = {"error": str(exc)[:200]}
                log.info("ponowienie %s - %s", kraj, wynik)
                # Nadpisujemy tylko wtedy, gdy druga proba faktycznie cos dala -
                # inaczej stracilibysmy tresc pierwszego bledu.
                if wynik.get("stored"):
                    podsumowanie[kraj] = wynik

    return podsumowanie

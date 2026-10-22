"""Kursy walut do raportowania wielu rynkow w jednej kwocie.

Konto sprzedaje w EUR, GBP, PLN i SEK. Dopoki patrzysz na jeden rynek, waluta
nie ma znaczenia - ale widok zbiorczy sumowal je dotad **jeden do jednego**,
wiec 3441 koron liczylo sie jako 3441 EUR i sprzedaz calkowita byla zawyzona.

Przeliczamy po **kursie z dnia transakcji**, nie po dzisiejszym: raport za
wrzesien ma zostac taki sam, gdy spojrzysz na niego w grudniu.

Zrodlem sa referencyjne kursy Europejskiego Banku Centralnego - publiczne,
bez klucza i bez limitow. EBC publikuje je w dni robocze okolo 16:00 CET,
wiec dla weekendow i swiat przenosimy ostatni znany kurs do przodu: tak samo
robi ksiegowosc i tak samo robi Amazon w raportach rozliczeniowych.
"""
from __future__ import annotations

import datetime as dt
import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from sqlalchemy import select

from ..db import session_scope
from ..models import FxRate, Profile

log = logging.getLogger(__name__)

# Kursy z ostatnich 90 dni - jeden maly plik, wystarcza do biezacych raportow
URL_90_DNI = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
# Pelna historia od 1999 roku (~10 MB) - tylko gdy ktos siega dalej wstecz
URL_HISTORIA = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"

NS = {
    "g": "http://www.gesmes.org/xml/2002-08-01",
    "e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
}

WALUTA_BAZOWA = "EUR"


def _pobierz(url: str) -> dict[dt.date, dict[str, float]]:
    """Kursy z pliku EBC: {dzien: {waluta: ile jednostek za 1 EUR}}."""
    with httpx.Client(timeout=60) as client:
        odp = client.get(url)
        odp.raise_for_status()
    root = ET.fromstring(odp.content)

    out: dict[dt.date, dict[str, float]] = {}
    for dzien in root.findall(".//e:Cube[@time]", NS):
        data = dt.date.fromisoformat(dzien.get("time"))
        kursy = {}
        for kurs in dzien.findall("e:Cube", NS):
            waluta, wartosc = kurs.get("currency"), kurs.get("rate")
            if waluta and wartosc:
                try:
                    kursy[waluta] = float(wartosc)
                except ValueError:
                    continue
        if kursy:
            out[data] = kursy
    return out


def waluty_konta() -> set[str]:
    """Waluty rynkow, ktore panel obsluguje - poza waluta bazowa."""
    with session_scope() as session:
        kody = {
            (k or "").upper()
            for (k,) in session.execute(select(Profile.currency_code).distinct()).all()
        }
    return {k for k in kody if k and k != WALUTA_BAZOWA}


def zapewnij_kursy(start: dt.date, end: dt.date, force: bool = False) -> dict[str, Any]:
    """Uzupelnia tabele kursow dla zakresu dat.

    Zwraca podsumowanie; nie rzuca wyjatkiem przy bledzie sieci - brak kursu
    oznacza tylko, ze kwota z danego rynku zostanie policzona bez przeliczenia
    (patrz `coalesce` w zapytaniach), a nie ze caly panel przestaje dzialac.
    """
    potrzebne = waluty_konta()
    if not potrzebne:
        return {"currencies": 0, "stored": 0}

    with session_scope() as session:
        mamy = {
            (d, w)
            for d, w in session.execute(
                select(FxRate.date, FxRate.currency)
                .where(FxRate.date >= start, FxRate.date <= end)
            ).all()
        }
    brakuje = [
        (start + dt.timedelta(days=i), w)
        for i in range((end - start).days + 1)
        for w in potrzebne
        if force or (start + dt.timedelta(days=i), w) not in mamy
    ]
    if not brakuje:
        return {"currencies": len(potrzebne), "stored": 0}

    try:
        surowe = _pobierz(URL_90_DNI)
        najstarszy = min(surowe) if surowe else end
        if start < najstarszy:
            # Siegamy dalej niz 90 dni - dopiero wtedy warto pobrac cala historie
            log.info("Kursy: zakres starszy niz 90 dni, pobieram pelna historie EBC")
            surowe.update(_pobierz(URL_HISTORIA))
    except Exception as exc:
        log.warning("Nie pobralem kursow z EBC: %s", exc)
        return {"currencies": len(potrzebne), "stored": 0, "error": str(exc)[:200]}

    if not surowe:
        return {"currencies": len(potrzebne), "stored": 0}

    # Dni bez notowania (weekendy, swieta) dostaja ostatni znany kurs. Bez tego
    # sprzedaz sobotnia i niedzielna wypadalaby z przeliczenia - czyli co trzeci
    # dzien raportu.
    dni_zrodla = sorted(surowe)
    wiersze: list[dict[str, Any]] = []
    biezace: dict[str, float] = {}
    i = 0
    dzien = min(start, dni_zrodla[0])
    while dzien <= end:
        while i < len(dni_zrodla) and dni_zrodla[i] <= dzien:
            biezace.update(surowe[dni_zrodla[i]])
            i += 1
        if dzien >= start:
            for waluta in potrzebne:
                za_euro = biezace.get(waluta)
                if za_euro:
                    wiersze.append({
                        "date": dzien,
                        "currency": waluta,
                        # Trzymamy "ile EUR za 1 jednostke" - tak sie mnozy kwote
                        "rate_eur": round(1.0 / za_euro, 8),
                    })
        dzien += dt.timedelta(days=1)

    with session_scope() as session:
        from .zapis import wstaw_lub_zastap
        zapisane = wstaw_lub_zastap(session, FxRate, wiersze)

    log.info("Kursy EBC: %s wierszy dla %s walut (%s..%s)",
             zapisane, len(potrzebne), start, end)
    return {"currencies": len(potrzebne), "stored": zapisane}


def odswiez(dni_wstecz: int = 120) -> dict[str, Any]:
    """Codzienne uzupelnienie - wolane przez scheduler i po starcie."""
    end = dt.date.today()
    return zapewnij_kursy(end - dt.timedelta(days=dni_wstecz), end)

"""Raport sprzedazy i ruchu z SP-API - mianownik do TACOS.

Ten sam schemat co przy raportach reklamowych: zamow, odpytuj, pobierz. Roznica
jest w tym, ze SP-API oddaje plik spakowany gzipem i czasem zaszyfrowany, a
zakres dat podaje sie z godzina.

Raport zwraca dwie sekcje: dzienna zbiorcza i per ASIN. Bierzemy obie - pierwsza
daje TACOS na poziomie rynku, druga per produkt.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import time
from typing import Any

import httpx

from .client import SpApiError, SpClient

log = logging.getLogger(__name__)

REPORT_TYPE = "GET_SALES_AND_TRAFFIC_REPORT"

# Raport potrafi sie liczyc kilka minut, przy dlugim zakresie dluzej.
POLL_INTERVAL_S = 20
POLL_TIMEOUT_S = 1800

# Amazon nie przyjmuje dowolnie dlugiego zakresu; dzielimy na kawalki.
MAX_WINDOW_DAYS = 60

DONE = {"DONE"}
FAILED = {"CANCELLED", "FATAL"}

# Amazon limituje samo ZAMAWIANIE raportow do okolo jednego na minute. Przy
# dziesieciu rynkach po dwa okna zapas burstowy konczy sie w polowie i reszta
# dostaje QuotaExceeded. Wymuszamy wiec odstep zamiast liczyc na ponawianie -
# ponawianie i tak trafia w ten sam limit, tylko pozniej.
MIN_CREATE_INTERVAL_S = 65
_ostatnie_zamowienie = 0.0


def _przepustnica() -> None:
    global _ostatnie_zamowienie
    minelo = time.time() - _ostatnie_zamowienie
    if _ostatnie_zamowienie and minelo < MIN_CREATE_INTERVAL_S:
        czekaj = MIN_CREATE_INTERVAL_S - minelo
        log.info("Odstep przed zamowieniem raportu: %.0fs", czekaj)
        time.sleep(czekaj)
    _ostatnie_zamowienie = time.time()


def date_windows(start: dt.date, end: dt.date, size: int = MAX_WINDOW_DAYS):
    cur = start
    while cur <= end:
        koniec = min(cur + dt.timedelta(days=size - 1), end)
        yield cur, koniec
        cur = koniec + dt.timedelta(days=1)


def _create(
    client: SpClient, marketplace_id: str, start: dt.date, end: dt.date,
    asin_granularity: str = "CHILD",
) -> str:
    body = {
        "reportType": REPORT_TYPE,
        "marketplaceIds": [marketplace_id],
        "dataStartTime": f"{start.isoformat()}T00:00:00Z",
        "dataEndTime": f"{end.isoformat()}T23:59:59Z",
        "reportOptions": {
            "dateGranularity": "DAY",
            "asinGranularity": asin_granularity,
        },
    }
    _przepustnica()
    resp = client.post("/reports/2021-06-30/reports", json=body)
    return resp["reportId"]


def _wait(client: SpClient, report_id: str) -> str:
    """Czeka na raport i zwraca identyfikator dokumentu."""
    do_kiedy = time.time() + POLL_TIMEOUT_S
    while time.time() < do_kiedy:
        stan = client.get(f"/reports/2021-06-30/reports/{report_id}")
        status = stan.get("processingStatus")
        if status in DONE:
            return stan["reportDocumentId"]
        if status in FAILED:
            raise SpApiError(0, f"Raport {report_id} zakonczyl sie stanem {status}", "reports")
        time.sleep(POLL_INTERVAL_S)
    raise SpApiError(0, f"Raport {report_id} nie ukonczyl sie w {POLL_TIMEOUT_S}s", "reports")


def _download(client: SpClient, document_id: str) -> dict[str, Any]:
    meta = client.get(f"/reports/2021-06-30/documents/{document_id}")
    url = meta["url"]

    raw = httpx.get(url, follow_redirects=True, timeout=180).content
    if meta.get("compressionAlgorithm") == "GZIP":
        raw = gzip.decompress(raw)
    else:
        # Amazon nie zawsze deklaruje kompresje, a i tak ja stosuje
        try:
            raw = gzip.decompress(raw)
        except (OSError, gzip.BadGzipFile):
            pass

    return json.loads(raw.decode("utf-8"))


def _kwota(pole: Any) -> float:
    """orderedProductSales przychodzi jako {amount, currencyCode}."""
    if isinstance(pole, dict):
        try:
            return float(pole.get("amount") or 0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(pole or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(pole: Any) -> int:
    try:
        return int(pole or 0)
    except (TypeError, ValueError):
        return 0


def to_rows(payload: dict[str, Any], profile_id: int) -> list[dict[str, Any]]:
    """Zamienia raport na wiersze sprzedazy calkowitej.

    Sekcja dzienna daje wiersze bez ASIN (zbiorcze dla rynku), sekcja per ASIN
    - wiersze z ASIN. Tabela traktuje pierwsze jako nadrzedne, wiec TACOS na
    poziomie rynku bierze sie z liczby Amazona, a nie z sumy pozycji, ktore
    akurat znalazly sie w raporcie.
    """
    rows: list[dict[str, Any]] = []

    for wpis in payload.get("salesAndTrafficByDate") or []:
        data = _parse_date(wpis.get("date"))
        if not data:
            continue
        sprzedaz = wpis.get("salesByDate") or {}
        ruch = wpis.get("trafficByDate") or {}
        rows.append({
            "date": data,
            "profile_id": profile_id,
            "asin": None,
            "sku": None,
            "sales": _kwota(sprzedaz.get("orderedProductSales")),
            "units": _int(sprzedaz.get("unitsOrdered")),
            "orders": _int(sprzedaz.get("totalOrderItems")),
            "sessions": _int(ruch.get("sessions")) or None,
            "source": "SPAPI",
        })

    # Sekcja per ASIN bywa rozna w zaleznosci od wersji raportu: czasem kazdy
    # wpis niesie wlasna date, czasem jest zagregowana po calym zakresie.
    # Sprawdzamy to zamiast zakladac - inaczej albo tracimy dane per produkt,
    # albo przypisujemy sprzedaz z dwoch miesiecy do jednego dnia.
    dni = sorted({r["date"] for r in rows})
    jedyny = dni[0] if len(dni) == 1 else None

    for wpis in payload.get("salesAndTrafficByAsin") or []:
        asin = (
            wpis.get("childAsin") or wpis.get("parentAsin") or wpis.get("sku") or ""
        ).strip().upper()
        if not asin:
            continue

        data = _parse_date(wpis.get("date")) or jedyny
        if data is None:
            # Zagregowane po calym zakresie i zakres jest dluzszy niz dzien -
            # nie da sie tego uczciwie rozlozyc na dni, wiec pomijamy.
            continue

        sprzedaz = wpis.get("salesByAsin") or {}
        ruch = wpis.get("trafficByAsin") or {}
        rows.append({
            "date": data,
            "profile_id": profile_id,
            "asin": asin,
            "sku": (wpis.get("sku") or None),
            "sales": _kwota(sprzedaz.get("orderedProductSales")),
            "units": _int(sprzedaz.get("unitsOrdered")),
            "orders": _int(sprzedaz.get("totalOrderItems")),
            "sessions": _int(ruch.get("sessions")) or None,
            "source": "SPAPI",
        })

    return rows


def asin_rows_possible(payload: dict[str, Any]) -> bool:
    """Czy raport da sie rozlozyc na produkty i dni.

    Sluzy do zdiagnozowania po pierwszym pobraniu, czy dane per ASIN maja
    wlasne daty. Jesli nie, TACOS per produkt wymaga pobierania dzien po dniu
    i trzeba o tym powiedziec wprost, zamiast cicho pokazywac puste kolumny.
    """
    wpisy = payload.get("salesAndTrafficByAsin") or []
    if not wpisy:
        return False
    return any(_parse_date(w.get("date")) for w in wpisy)


def _parse_date(raw: Any) -> dt.date | None:
    if not raw:
        return None
    tekst = str(raw)[:10]
    try:
        return dt.date.fromisoformat(tekst)
    except ValueError:
        return None


def fetch_window(
    client: SpClient, marketplace_id: str, profile_id: int,
    start: dt.date, end: dt.date,
) -> list[dict[str, Any]]:
    """Pobiera jedno okno i zwraca gotowe wiersze."""
    rid = _create(client, marketplace_id, start, end)
    log.info("Raport %s %s..%s zamowiony (%s)", marketplace_id, start, end, rid)
    doc = _wait(client, rid)
    payload = _download(client, doc)
    rows = to_rows(payload, profile_id)
    log.info("Raport %s %s..%s: %s wierszy", marketplace_id, start, end, len(rows))
    return rows

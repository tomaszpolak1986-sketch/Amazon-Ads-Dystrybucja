"""Raport zamowien z SP-API - podzial sprzedazy na FBA i wysylke wlasna.

Raport sprzedazy (`sales_report`) nie zna kanalu realizacji: ma rozbicia B2B,
zwroty i wysylki, ale nie mowi, ktore zamowienie poszlo z magazynu Amazona,
a ktore z naszego. Sprawdzone na zywym raporcie - takiego pola tam po prostu
nie ma. Jest za to tutaj, w `GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL`,
jako kolumna `fulfillment-channel` o dwoch wartosciach: `Amazon` i `Merchant`.

Trzy rzeczy, ktore ten raport robi inaczej niz reszta i ktore latwo przeoczyc:

* **Jest kontem, nie rynkiem.** Zamowiony dla jednego marketplace zwraca
  zamowienia ze WSZYSTKICH - zmierzone: zapytanie o DE oddalo Amazon.de,
  .fr, .it, .es, .co.uk i .nl naraz. Dlatego pobieramy go RAZ, a rynek
  ustalamy z kolumny `sales-channel`. Pobieranie per rynek dawaloby te same
  zamowienia dziesiec razy i dziesieciokrotnie obciazalo limit.
* **Miesza waluty.** W jednym pliku sa EUR, GBP i SEK. Sumowanie ich razem
  dawaloby liczbe bez znaczenia, wiec agregujemy per rynek, ktory ma
  jedna walute.
* **To plik TSV, nie JSON**, i zawiera dane kupujacych: miasto, kod pocztowy,
  nazwe firmy. Nie zapisujemy z niego ani jednego takiego pola - wychodza
  stad wylacznie sumy per dzien, rynek i kanal.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import logging
from collections import defaultdict
from typing import Any

import httpx

from .client import SpClient

log = logging.getLogger(__name__)

REPORT_TYPE = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL"

# Wartosci kolumny fulfillment-channel. Sprawdzone na danych konta.
KANAL_FBA = "Amazon"
KANAL_WLASNY = "Merchant"

# Zamowienia anulowane nie sa sprzedaza. Reszta statusow (Shipped, Unshipped,
# a takze pusty) to zamowienia zlozone i liczymy je normalnie - tak samo robi
# raport sprzedazy, ktory bierze `unitsOrdered`, nie `unitsShipped`.
STATUS_POMIJANY = "cancelled"

# sales-channel -> kod kraju. Amazon zapisuje go jako domene sklepu.
KANAL_NA_KRAJ = {
    "amazon.de": "DE", "amazon.fr": "FR", "amazon.it": "IT", "amazon.es": "ES",
    "amazon.nl": "NL", "amazon.se": "SE", "amazon.pl": "PL", "amazon.be": "BE",
    "amazon.com.tr": "TR", "amazon.co.uk": "UK", "amazon.com": "US",
    "amazon.ie": "IE",
}


def kraj_z_kanalu(kanal: str | None) -> str | None:
    if not kanal:
        return None
    return KANAL_NA_KRAJ.get(kanal.strip().lower())


def utworz(client: SpClient, marketplace_ids: list[str], start: dt.date, end: dt.date) -> str:
    """Zamawia raport. `end` wlacznie - stad koniec doby, nie polnoc."""
    body = {
        "reportType": REPORT_TYPE,
        "marketplaceIds": marketplace_ids,
        "dataStartTime": f"{start.isoformat()}T00:00:00Z",
        "dataEndTime": f"{end.isoformat()}T23:59:59Z",
    }
    resp = client.post("/reports/2021-06-30/reports", json=body)
    return resp["reportId"]


def pobierz_tekst(client: SpClient, document_id: str) -> str:
    meta = client.get(f"/reports/2021-06-30/documents/{document_id}")
    raw = httpx.get(meta["url"], follow_redirects=True, timeout=180).content
    if meta.get("compressionAlgorithm") == "GZIP" or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except (OSError, gzip.BadGzipFile):
            pass
    # Amazon deklaruje kodowanie w metadanych, ale bywa ono rozne per rynek;
    # bledne znaki wolimy zastapic niz wywrocic caly przebieg.
    return raw.decode(meta.get("charset") or "utf-8", "replace")


def _liczba(tekst: str | None) -> float:
    try:
        return float((tekst or "").strip() or 0)
    except ValueError:
        return 0.0


def _data(tekst: str | None) -> dt.date | None:
    """Dzien zakupu. Bierzemy sama date z sygnatury ISO."""
    if not tekst:
        return None
    try:
        return dt.date.fromisoformat(str(tekst)[:10])
    except ValueError:
        return None


def agreguj(tekst: str) -> list[dict[str, Any]]:
    """TSV -> sumy per (dzien, kraj, kanal realizacji).

    Zwraca wiersze gotowe do zapisu. Zadne pole kupujacego nie opuszcza tej
    funkcji - wchodzi caly plik, wychodza wylacznie sumy.
    """
    czytnik = csv.DictReader(io.StringIO(tekst), delimiter="\t")

    kubelki: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"sales": 0.0, "units": 0, "orders": set(), "currency": None}
    )
    pominietych = 0

    for wiersz in czytnik:
        if (wiersz.get("item-status") or "").strip().lower() == STATUS_POMIJANY:
            continue

        data = _data(wiersz.get("purchase-date"))
        kraj = kraj_z_kanalu(wiersz.get("sales-channel"))
        if not data or not kraj:
            # Bez daty albo bez rozpoznanego rynku nie ma gdzie tego przypisac.
            pominietych += 1
            continue

        kanal = (wiersz.get("fulfillment-channel") or "").strip()
        kanal = "FBA" if kanal == KANAL_FBA else "MERCHANT" if kanal == KANAL_WLASNY else "INNY"

        klucz = (data, kraj, kanal)
        poz = kubelki[klucz]
        # item-price to wartosc pozycji, juz przemnozona przez ilosc.
        poz["sales"] += _liczba(wiersz.get("item-price"))
        poz["units"] += int(_liczba(wiersz.get("quantity")))
        poz["orders"].add(wiersz.get("amazon-order-id") or "")
        poz["currency"] = poz["currency"] or (wiersz.get("currency") or "").strip() or None

    if pominietych:
        log.info("Raport zamowien: pominieto %s wierszy bez daty lub rynku", pominietych)

    return [
        {
            "date": data, "country": kraj, "channel": kanal,
            "sales": round(poz["sales"], 2),
            "units": poz["units"],
            "orders": len(poz["orders"] - {""}),
            "currency": poz["currency"],
        }
        for (data, kraj, kanal), poz in sorted(kubelki.items())
    ]

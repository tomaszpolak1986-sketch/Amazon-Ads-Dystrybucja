"""Raport ofert z Seller Central - pełna baza produktowa.

Ads API zna tylko produkty, ktore kiedys trafily do kampanii. Zeby zobaczyc
caly asortyment - w tym to, czego nigdy nie reklamowano - trzeba siegnac do
raportu `GET_MERCHANT_LISTINGS_ALL_DATA` w SP-API.

Uklad tego raportu jest zaskakujaco niestabilny i wszystko ponizej wynika
z tego, co Amazon naprawde zwrocil, a nie z dokumentacji:

* **Liczba i zestaw kolumn roznia sie miedzy rynkami tego samego konta.**
  DE zwraca 32 kolumny, NL 28, a FR tylko 18 - bez `image-url`, bez `zshop-*`
  i **bez kolumny ASIN**. Czytanie po numerze kolumny nie ma wiec szans.
* **Nazwy naglowkow ida za jezykiem konta, nie rynku.** Raport dla Francji ma
  naglowki po niemiecku ("Händler-SKU"), a dane po francusku. Dlatego kazde
  pole ma liste aliasow, nie jedna nazwe.
* **Na FR ASIN trzeba wyluskac z `Produkt-ID`** - i tylko wtedy, gdy
  `Produkt-ID-Typ` wynosi 1. Przy innym typie jest tam EAN, ktory ASIN-em nie
  jest i nie wolno go tak potraktowac.
* **`Status` nie ma stalej pozycji ani przetlumaczalnej nazwy**, za to jego
  wartosci sa po angielsku niezaleznie od jezyka naglowkow. Wykrywamy go po
  zawartosci kolumny.

Gdy zabraknie kolumny, bez ktorej wiersz nie ma sensu, parser przerywa
z wyjasnieniem - zamiast po cichu zapisac przesuniete dane.
"""
from __future__ import annotations

import datetime as dt
import gzip
import logging
import re
import time
from typing import Any

import httpx

from .client import SpApiError, SpClient

log = logging.getLogger(__name__)

REPORT_TYPE = "GET_MERCHANT_LISTINGS_ALL_DATA"

# Amazon odrzuca zbyt czeste tworzenie raportow (QuotaExceeded), wiec
# odczekujemy miedzy rynkami.
MIN_CREATE_INTERVAL_S = 65
_ostatnie_utworzenie = 0.0

POLL_INTERVAL_S = 8
MAX_POLL_S = 900

# Aliasy naglowkow. Angielskie sa oryginalne, niemieckie wystepuja na tym
# koncie, reszta na wypadek zmiany jezyka w Seller Central.
ALIASY: dict[str, tuple[str, ...]] = {
    "title": (
        "item-name", "artikelbezeichnung", "nom-de-l'article", "nombre-del-producto",
        "nome-articolo", "productnaam",
    ),
    "listing_id": ("listing-id", "angebotsnummer", "numero-de-l'offre"),
    "sku": (
        "seller-sku", "händler-sku", "haendler-sku", "sku-du-vendeur",
        "sku-del-vendedor", "sku-venditore",
    ),
    "price": ("price", "preis", "prix", "precio", "prezzo", "prijs"),
    "quantity": ("quantity", "menge", "quantité", "cantidad", "quantità", "aantal"),
    "opened_on": (
        "open-date", "erstellungsdatum", "date-d'ouverture", "fecha-de-apertura",
    ),
    "fulfillment": (
        "fulfillment-channel", "versender", "circuit-d'exécution", "canal-logistico",
    ),
    "asin": ("asin1", "asin 1"),
    "product_id": ("product-id", "produkt-id"),
    "product_id_type": ("product-id-type", "produkt-id-typ"),
}

# Bez tych pol wiersz nie niesie zadnej uzytecznej informacji.
WYMAGANE = ("title", "sku")

# Wartosci statusu sa po angielsku nawet przy niemieckich naglowkach.
STATUSY = {"active", "inactive", "incomplete"}

# W `Produkt-ID` ASIN siedzi tylko przy tym typie identyfikatora.
TYP_ID_ASIN = "1"

ASIN_WZOR = re.compile(r"^B[0-9A-Z]{9}$")


class UkladRaportuZmieniony(RuntimeError):
    """Brakuje kolumny, bez ktorej nie da sie uczciwie odczytac raportu."""


def _przepustnica() -> None:
    global _ostatnie_utworzenie
    czekaj = MIN_CREATE_INTERVAL_S - (time.time() - _ostatnie_utworzenie)
    if czekaj > 0:
        log.info("Czekam %.0f s przed kolejnym raportem ofert", czekaj)
        time.sleep(czekaj)
    _ostatnie_utworzenie = time.time()


def _mapuj_kolumny(naglowek: list[str]) -> dict[str, int]:
    """Nazwa pola -> numer kolumny, po aliasach."""
    pozycje = {k.strip().lower(): i for i, k in enumerate(naglowek)}
    mapa: dict[str, int] = {}
    for pole, aliasy in ALIASY.items():
        for alias in aliasy:
            if alias in pozycje:
                mapa[pole] = pozycje[alias]
                break

    brak = [p for p in WYMAGANE if p not in mapa]
    if brak:
        raise UkladRaportuZmieniony(
            f"Brak kolumn {brak} w naglowku: {naglowek[:8]}…"
        )
    return mapa


def _znajdz_status(wiersze: list[list[str]]) -> int | None:
    """Numer kolumny ze statusem oferty - rozpoznany po wartosciach.

    Nazwa nie pomoze (idzie za jezykiem konta), numer tez nie (rozni sie
    miedzy rynkami). Same wartosci sa za to angielskie i jest ich kilka, wiec
    kolumna statusu odroznia sie od kazdej innej.
    """
    if not wiersze:
        return None
    najlepsza, najlepszy_udzial = None, 0.0
    szerokosc = min(len(w) for w in wiersze)
    for kol in range(szerokosc):
        wartosci = [w[kol].strip().lower() for w in wiersze if w[kol].strip()]
        if not wartosci:
            continue
        udzial = sum(1 for v in wartosci if v in STATUSY) / len(wartosci)
        if udzial > najlepszy_udzial:
            najlepsza, najlepszy_udzial = kol, udzial
    # Ponizej polowy trafien to nie jest kolumna statusu, tylko przypadek.
    return najlepsza if najlepszy_udzial >= 0.5 else None


def _pole(pola: list[str], mapa: dict[str, int], nazwa: str) -> str:
    kol = mapa.get(nazwa)
    if kol is None or kol >= len(pola):
        return ""
    return pola[kol].strip()


def _asin(pola: list[str], mapa: dict[str, int]) -> str | None:
    """ASIN z kolumny asin1, a gdy jej nie ma - z `Produkt-ID`.

    Na FR kolumny ASIN nie ma wcale. `Produkt-ID` zawiera wtedy ASIN, ale
    tylko przy typie identyfikatora 1 - przy innym jest tam EAN i wziecie go
    za ASIN podpielo by pod produkt cudze dane katalogowe.
    """
    wprost = _pole(pola, mapa, "asin")
    if wprost:
        return wprost.upper() or None

    identyfikator = _pole(pola, mapa, "product_id").upper()
    if not identyfikator:
        return None
    typ = _pole(pola, mapa, "product_id_type")
    # Typ bywa pusty; wtedy ufamy ksztaltowi, ktory dla ASIN-a jest jednoznaczny.
    if typ and typ != TYP_ID_ASIN:
        return None
    return identyfikator if ASIN_WZOR.match(identyfikator) else None


def _liczba(tekst: str) -> float | None:
    tekst = (tekst or "").strip().replace(",", ".")
    if not tekst:
        return None
    try:
        return float(tekst)
    except ValueError:
        return None


def _calkowita(tekst: str) -> int | None:
    wartosc = _liczba(tekst)
    return int(wartosc) if wartosc is not None else None


def _data(tekst: str) -> dt.date | None:
    """Data otwarcia oferty: '20/09/2023 12:38:43 MEST' albo format ISO."""
    czesc = (tekst or "").strip().split(" ")[0]
    if not czesc:
        return None
    for wzor in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(czesc, wzor).date()
        except ValueError:
            continue
    return None


def _pobierz_dokument(client: SpClient, document_id: str) -> str:
    doc = client.get(f"/reports/2021-06-30/documents/{document_id}")
    surowe = httpx.get(doc["url"], timeout=180).content
    if doc.get("compressionAlgorithm") == "GZIP":
        surowe = gzip.decompress(surowe)
    # Raport jest w UTF-8 z BOM; `utf-8-sig` zdejmuje go z pierwszej kolumny.
    return surowe.decode("utf-8-sig", errors="replace")


def _czekaj_na_raport(client: SpClient, report_id: str) -> dict[str, Any]:
    czekano = 0
    while czekano < MAX_POLL_S:
        stan = client.get(f"/reports/2021-06-30/reports/{report_id}")
        status = stan.get("processingStatus")
        if status == "DONE":
            return stan
        if status in ("FATAL", "CANCELLED"):
            raise SpApiError(200, f"Raport zakonczony jako {status}", REPORT_TYPE)
        time.sleep(POLL_INTERVAL_S)
        czekano += POLL_INTERVAL_S
    raise SpApiError(200, "Raport nie zdazyl sie policzyc", REPORT_TYPE)


def fetch(client: SpClient, marketplace_id: str) -> list[dict[str, Any]]:
    """Pobiera i parsuje raport ofert dla jednego rynku."""
    _przepustnica()
    utworzony = client.post(
        "/reports/2021-06-30/reports",
        json={"reportType": REPORT_TYPE, "marketplaceIds": [marketplace_id]},
    )
    stan = _czekaj_na_raport(client, utworzony["reportId"])

    linie = _pobierz_dokument(client, stan["reportDocumentId"]).splitlines()
    if not linie:
        return []

    naglowek = linie[0].split("\t")
    mapa = _mapuj_kolumny(naglowek)

    wiersze = [linia.split("\t") for linia in linie[1:] if linia.strip()]
    if not wiersze:
        return []

    # Status wykrywamy z probki, nie z calosci - dwiescie wierszy wystarczy,
    # zeby kolumna sie ujawnila.
    kol_status = _znajdz_status(wiersze[:200])
    if kol_status is None:
        log.warning(
            "Nie rozpoznano kolumny ze statusem dla %s - zapisze oferty bez niego",
            marketplace_id,
        )

    out: list[dict[str, Any]] = []
    for pola in wiersze:
        sku = _pole(pola, mapa, "sku")
        if not sku:
            continue
        status = None
        if kol_status is not None and kol_status < len(pola):
            status = pola[kol_status].strip() or None
        out.append({
            "sku": sku,
            "marketplace_id": marketplace_id,
            "asin": _asin(pola, mapa),
            "title": _pole(pola, mapa, "title") or None,
            "price": _liczba(_pole(pola, mapa, "price")),
            "quantity": _calkowita(_pole(pola, mapa, "quantity")),
            "status": status,
            "fulfillment": _pole(pola, mapa, "fulfillment") or None,
            "listing_id": _pole(pola, mapa, "listing_id") or None,
            "opened_on": _data(_pole(pola, mapa, "opened_on")),
        })
    return out

"""Reporting API v3 - asynchroniczne raporty Sponsored Products.

Przeplyw: POST /reporting/reports -> polling GET /reporting/reports/{id} ->
pobranie presigned URL (gzip JSON) -> lista dictow.

Uwagi praktyczne:
  * maksymalne okno dla timeUnit=DAILY to 31 dni - dluzsze zakresy tniemy na kawalki,
  * retencja danych to ~95 dni wstecz,
  * atrybucja dojrzewa kilka dni, wiec ostatnie dni odswiezamy przy kazdym syncu,
  * zestawy kolumn roznia sie per reportTypeId; kolumny opcjonalne sa odrzucane
    przez API bledem 400, dlatego przy 400 ponawiamy zapytanie na samym rdzeniu.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import logging
import re
import time
from typing import Any

import httpx

from .client import AdsApiError, AdsClient

log = logging.getLogger(__name__)

CREATE_MEDIA = "application/vnd.createasyncreportrequest.v3+json"
MAX_WINDOW_DAYS = 31
# Raport powstaje u Amazona okolo 20 minut, wiec odpytywanie co 15 sekund to
# ~80 zapytan na raport i szesc raportow naraz - stad bledy 429. Przy takim
# czasie oczekiwania sprawdzanie co minute opoznia wynik najwyzej o minute,
# a ruch spada czterokrotnie. Start jest szybki, na wypadek raportu gotowego
# od razu; dopiero potem odstep rosnie.
POLL_INTERVAL_S = 60
# Raport produktowy przy kilku tysiacach reklam potrafi generowac sie ponad
# pol godziny - przy 30-minutowym limicie okno miesieczne DE nie zdazylo.
POLL_TIMEOUT_S = 3600

# Amazon odrzuca powtorzone zlecenie o tej samej konfiguracji bledem 425 i podaje
# w tresci identyfikator raportu, ktory juz istnieje. To nie jest blad - to
# oszczednosc: zamiast generowac drugi raz, uzywamy tego, ktory juz powstal.
DUPLICATE_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_BASE_METRICS = [
    "impressions",
    "clicks",
    "cost",
    "purchases7d",
    "sales7d",
    "unitsSoldClicks7d",
    "purchasesSameSku7d",
    "attributedSalesSameSku7d",
]


class ReportSpec:
    """Definicja jednego typu raportu."""

    def __init__(
        self,
        key: str,
        report_type_id: str,
        group_by: list[str],
        core_columns: list[str],
        optional_columns: list[str] | None = None,
        ad_product: str = "SPONSORED_PRODUCTS",
    ) -> None:
        self.key = key
        self.report_type_id = report_type_id
        self.group_by = group_by
        self.core_columns = core_columns
        self.optional_columns = optional_columns or []
        self.ad_product = ad_product

    @property
    def all_columns(self) -> list[str]:
        return self.core_columns + self.optional_columns


# Metryki SB i SD nazywaja sie inaczej niz w SP: nie ma sufiksu `7d`, bo
# okno atrybucji jest wlasciwoscia raportu, nie kolumny. Nie ma tez rozbicia
# na sprzedaz wlasnego i cudzego SKU - efektu halo w SB i SD po prostu nie da
# sie policzyc z tych danych.
_SB_SD_METRICS = ["impressions", "clicks", "cost", "purchases", "sales", "unitsSold"]

# Sponsored Brands ma metryki, ktorych SP nie zna: ilu kupujacych to nowi
# klienci marki. To glowny powod, dla ktorego SB w ogole sie prowadzi, wiec
# zamawiamy je od poczatku.
_SB_NTB = ["newToBrandPurchases", "newToBrandSales", "newToBrandUnitsSold"]

SB_SPECS: dict[str, ReportSpec] = {
    "campaign": ReportSpec(
        key="campaign",
        report_type_id="sbCampaigns",
        group_by=["campaign"],
        core_columns=["date", "campaignId", *_SB_SD_METRICS],
        optional_columns=[*_SB_NTB, "campaignName", "campaignStatus",
                          "campaignBudgetAmount", "topOfSearchImpressionShare"],
        ad_product="SPONSORED_BRANDS",
    ),
    "targeting": ReportSpec(
        key="targeting",
        report_type_id="sbTargeting",
        group_by=["targeting"],
        core_columns=["date", "campaignId", "adGroupId", "keywordId", *_SB_SD_METRICS],
        optional_columns=[*_SB_NTB, "keywordText", "keywordType", "matchType",
                          "keywordBid", "targetingId", "targetingText",
                          "targetingType", "topOfSearchImpressionShare"],
        ad_product="SPONSORED_BRANDS",
    ),
    "search_term": ReportSpec(
        key="search_term",
        report_type_id="sbSearchTerm",
        group_by=["searchTerm"],
        core_columns=["date", "campaignId", "adGroupId", "keywordId", "searchTerm",
                      *_SB_SD_METRICS],
        optional_columns=["keywordText", "matchType"],
        ad_product="SPONSORED_BRANDS",
    ),
}

# Sponsored Display rozdziela atrybucje z klikniec i z wyswietlen. `sales`
# obejmuje oba, `salesClicks` tylko klikniecia - to nie jest ta sama miara co
# w SP i mieszanie ich dawaloby falszywy ACOS. Zamawiamy obie i zapisujemy
# `sales` jako glowna, zgodnie z tym, co pokazuje konsola Amazona.
SD_SPECS: dict[str, ReportSpec] = {
    "campaign": ReportSpec(
        key="campaign",
        report_type_id="sdCampaigns",
        group_by=["campaign"],
        core_columns=["date", "campaignId", *_SB_SD_METRICS],
        optional_columns=["campaignName", "salesClicks", "purchasesClicks",
                          "impressionsViews", "viewabilityRate"],
        ad_product="SPONSORED_DISPLAY",
    ),
    "targeting": ReportSpec(
        key="targeting",
        report_type_id="sdTargeting",
        group_by=["targeting"],
        core_columns=["date", "campaignId", "adGroupId", "targetingId", *_SB_SD_METRICS],
        optional_columns=["targetingText", "targetingExpression",
                          "salesClicks", "purchasesClicks", "impressionsViews"],
        ad_product="SPONSORED_DISPLAY",
    ),
    "product": ReportSpec(
        key="product",
        report_type_id="sdAdvertisedProduct",
        group_by=["advertiser"],
        core_columns=["date", "campaignId", "adGroupId", *_SB_SD_METRICS],
        optional_columns=["promotedAsin", "promotedSku", "adId",
                          "salesClicks", "purchasesClicks"],
        ad_product="SPONSORED_DISPLAY",
    ),
}


SPECS: dict[str, ReportSpec] = {
    "campaign": ReportSpec(
        key="campaign",
        report_type_id="spCampaigns",
        group_by=["campaign"],
        core_columns=["date", "campaignId", *_BASE_METRICS],
        optional_columns=[
            "campaignName",
            "campaignStatus",
            "campaignBudgetAmount",
            "campaignBudgetType",
            "campaignBiddingStrategy",
            "topOfSearchImpressionShare",
        ],
    ),
    "placement": ReportSpec(
        key="placement",
        report_type_id="spCampaigns",
        group_by=["campaignPlacement"],
        core_columns=["date", "campaignId", "placementClassification", *_BASE_METRICS],
        # topOfSearchImpressionShare jest na liscie dozwolonych kolumn tego
        # raportu, ale przy grupowaniu po placemencie Amazon nigdy go nie
        # wypelnia - sprawdzone na ponad 18 tys. wierszy z szesciu rynkow,
        # zero wartosci. Nie zamawiamy go, zeby nie udawac, ze dane sa.
        optional_columns=["campaignName"],
    ),
    "targeting": ReportSpec(
        key="targeting",
        report_type_id="spTargeting",
        group_by=["targeting"],
        core_columns=["date", "campaignId", "adGroupId", "keywordId", *_BASE_METRICS],
        optional_columns=[
            "campaignName",
            "adGroupName",
            "keyword",
            "keywordType",
            "matchType",
            "targeting",
            "keywordBid",
            "adKeywordStatus",
            "topOfSearchImpressionShare",
            "salesOtherSku7d",
            "unitsSoldOtherSku7d",
        ],
    ),
    "search_term": ReportSpec(
        key="search_term",
        report_type_id="spSearchTerm",
        group_by=["searchTerm"],
        core_columns=[
            "date", "campaignId", "adGroupId", "keywordId", "searchTerm", *_BASE_METRICS,
        ],
        optional_columns=[
            "campaignName", "adGroupName", "keyword", "matchType", "targeting", "adKeywordStatus",
            "salesOtherSku7d",
            "unitsSoldOtherSku7d",
        ],
    ),
    "product": ReportSpec(
        key="product",
        report_type_id="spAdvertisedProduct",
        group_by=["advertiser"],
        core_columns=["date", "campaignId", "adGroupId", "advertisedSku", *_BASE_METRICS],
        optional_columns=[
            "campaignName", "adGroupName", "advertisedAsin",
            "salesOtherSku7d",
            "unitsSoldOtherSku7d",
        ],
    ),
}


def _columns_tag(columns: list[str]) -> str:
    """Krotki skrot zestawu kolumn - odroznia raporty o roznej zawartosci."""
    podpis = ",".join(sorted(columns))
    return hashlib.sha1(podpis.encode("utf-8")).hexdigest()[:8]


def date_windows(start: dt.date, end: dt.date, size: int = MAX_WINDOW_DAYS):
    """Tnie zakres dat na kawalki miesciace sie w limicie API."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=size - 1), end)
        yield cur, chunk_end
        cur = chunk_end + dt.timedelta(days=1)


def _create(client: AdsClient, spec: ReportSpec, start: dt.date, end: dt.date,
            columns: list[str]) -> str:
    body = {
        # Skrot listy kolumn w nazwie. Amazon potrafi oddac istniejacy raport
        # o tej samej konfiguracji zamiast liczyc nowy - a wtedy po dodaniu
        # kolumny wraca stara wersja, bez niej, i to bez zadnego bledu.
        # FR wlasnie tak przepadl przy pierwszym pobraniu efektu halo.
        "name": f"adspanel-{spec.key}-{start}-{end}-{_columns_tag(columns)}",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "configuration": {
            "adProduct": spec.ad_product,
            "groupBy": spec.group_by,
            "columns": columns,
            "reportTypeId": spec.report_type_id,
            "timeUnit": "DAILY",
            "format": "GZIP_JSON",
        },
    }
    try:
        resp = client.post(
            "/reporting/reports", json=body, accept=CREATE_MEDIA, content_type=CREATE_MEDIA
        )
    except AdsApiError as exc:
        if exc.status == 425:
            match = DUPLICATE_ID.search(exc.message)
            if match:
                log.info("Raport %s juz zlecony, uzywam istniejacego: %s", spec.key, match.group())
                return match.group()
        raise

    report_id = (resp or {}).get("reportId")
    if not report_id:
        raise AdsApiError(0, f"Brak reportId w odpowiedzi: {resp}", "/reporting/reports")
    return report_id


def _wait(client: AdsClient, report_id: str, timeout_s: int = POLL_TIMEOUT_S) -> str:
    deadline = time.time() + timeout_s
    delay = 5
    while time.time() < deadline:
        status = client.get(f"/reporting/reports/{report_id}") or {}
        state = (status.get("status") or "").upper()

        if state == "COMPLETED":
            url = status.get("url")
            if not url:
                raise AdsApiError(0, f"Raport {report_id} gotowy, ale bez URL: {status}", "")
            return url
        if state in {"FAILED", "CANCELLED"}:
            raise AdsApiError(
                0, f"Raport {report_id} zakonczony statusem {state}: "
                   f"{status.get('failureReason') or status}", ""
            )

        time.sleep(delay)
        delay = min(POLL_INTERVAL_S, delay * 2)

    raise TimeoutError(f"Raport {report_id} nie ukonczyl sie w {timeout_s}s")


def _download(url: str) -> list[dict[str, Any]]:
    """Presigned URL S3 - bez naglowkow autoryzacji."""
    with httpx.Client(timeout=180.0, follow_redirects=True) as http:
        resp = http.get(url)
        resp.raise_for_status()
        raw = resp.content

    if raw[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
            raw = fh.read()

    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict):  # niektore typy pakuja wiersze pod kluczem
        for key in ("rows", "data", "records"):
            if key in data:
                return data[key]
        return []
    return data


def fetch_window(
    client: AdsClient,
    spec_key: str,
    start: dt.date,
    end: dt.date,
    specs: dict[str, "ReportSpec"] | None = None,
) -> list[dict[str, Any]]:
    """Pobiera JEDNO okno jednego raportu: zlecenie -> oczekiwanie -> pobranie.

    Wydzielone z fetch_report, zeby warstwa wyzej mogla puscic wiele okien
    rownolegle. Waskim gardlem jest generowanie po stronie Amazona (5-20 min),
    wiec czekanie na kilka raportow naraz skraca calosc wielokrotnie.
    """
    # `specs` pozwala uzyc tego samego mechanizmu dla SB i SD, ktore maja
    # inne typy raportow i inne kolumny, ale identyczny przebieg:
    # zlecenie -> oczekiwanie -> pobranie.
    spec = (specs or SPECS)[spec_key]
    columns = spec.all_columns

    try:
        report_id = _create(client, spec, start, end, columns)
    except AdsApiError as exc:
        if exc.status != 400:
            raise
        log.warning(
            "Raport %s: API odrzucilo zestaw kolumn (%s). Ponawiam na kolumnach rdzeniowych.",
            spec_key, exc.message[:200],
        )
        report_id = _create(client, spec, start, end, spec.core_columns)

    log.info("Raport %s %s..%s -> id=%s", spec_key, start, end, report_id)
    rows = _download(_wait(client, report_id))
    log.info("Raport %s %s..%s: %s wierszy", spec_key, start, end, len(rows))
    return rows


def fetch_report(
    client: AdsClient,
    spec_key: str,
    start: dt.date,
    end: dt.date,
) -> list[dict[str, Any]]:
    """Pobiera raport dla calego zakresu, sekwencyjnie okno po oknie."""
    rows: list[dict[str, Any]] = []
    for win_start, win_end in date_windows(start, end):
        rows.extend(fetch_window(client, spec_key, win_start, win_end))
    return rows

"""Raporty Sponsored Brands i Sponsored Display.

Przebieg jest ten sam co przy SP — zlecenie, oczekiwanie, pobranie — więc
korzystamy z `reports.fetch_window`. Różni się to, co przychodzi w środku,
i to na tyle, że wspólny mapper metryk byłby błędem:

* **Nazwy metryk nie mają sufiksu `7d`.** SP oddaje `purchases7d` i `sales7d`,
  SB i SD po prostu `purchases` i `sales` — okno atrybucji jest właściwością
  raportu, nie kolumny.
* **Nie ma rozbicia na własne i cudze SKU.** Efektu halo w SB i SD nie da się
  policzyć z tych danych, więc te kolumny zostają puste, a nie zerowe.
* **SD rozdziela atrybucję z kliknięć i z wyświetleń.** `sales` obejmuje oba,
  `salesClicks` tylko kliknięcia. Zapisujemy `sales`, bo to ta wartość widnieje
  w konsoli Amazona — ale trzeba wiedzieć, że nie znaczy dokładnie tego samego
  co `sales7d` w SP.
* **SB ma metryki „nowych klientów marki".** To zwykle powód, dla którego SB
  się w ogóle prowadzi. Zamawiamy je i trzymamy w `raw` — na osobne kolumny
  przyjdzie czas, gdy będzie na czym je pokazać.

Raporty SB potrafią liczyć się **19 minut** — zmierzone. To nie awaria, tylko
ich normalne tempo; okna pobieramy równolegle, tak jak przy SP.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..ads.client import AdsClient
from ..ads import reports
from ..db import session_scope
from ..models import CampaignDaily, ProductDaily, SearchTermDaily, TargetDaily
from .sync import _date, _dedupe, _f, _i, _replace_range, _s

log = logging.getLogger(__name__)


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Metryki SB/SD -> kolumny MetricsMixin."""
    return {
        "impressions": _i(row.get("impressions")),
        "clicks": _i(row.get("clicks")),
        "cost": _f(row.get("cost")),
        "orders": _i(row.get("purchases")),
        "sales": _f(row.get("sales")),
        "units": _i(row.get("unitsSold")),
        # SB i SD nie rozdzielaja sprzedazy wlasnego SKU od reszty, wiec
        # halo zostaje puste - brak danych, nie brak efektu.
        "orders_same_sku": 0,
        "sales_same_sku": 0.0,
        "sales_other_sku": None,
        "units_other_sku": None,
        "tos_impression_share": (
            float(row["topOfSearchImpressionShare"])
            if row.get("topOfSearchImpressionShare") not in (None, "")
            else None
        ),
    }


def _rows_campaign(pid: int, ad_product: str, raw: list[dict[str, Any]]):
    return _dedupe(
        [
            {
                "date": _date(r.get("date")),
                "profile_id": pid,
                "campaign_id": _s(r.get("campaignId")),
                "ad_product": ad_product,
                **_metrics(r),
            }
            for r in raw
            if _date(r.get("date"))
        ],
        ("date", "profile_id", "campaign_id"),
    )


def _rows_targeting(pid: int, ad_product: str, raw: list[dict[str, Any]]):
    wiersze = []
    for r in raw:
        if not _date(r.get("date")):
            continue
        # SB raportuje keywordy i targety osobnymi kolumnami; SD ma tylko
        # targety. Bierzemy to, co jest, zeby jeden mapper obsluzyl oba.
        tid = _s(r.get("keywordId")) or _s(r.get("targetingId"))
        if not tid:
            continue
        wiersze.append({
            "date": _date(r.get("date")),
            "profile_id": pid,
            "campaign_id": _s(r.get("campaignId")),
            "ad_group_id": _s(r.get("adGroupId")),
            "target_id": tid,
            "ad_product": ad_product,
            "target_kind": "KEYWORD" if r.get("keywordId") else "TARGET",
            "target_text": r.get("keywordText") or r.get("targetingText"),
            "match_type": r.get("matchType"),
            "bid": _f(r.get("keywordBid")) or None,
            **_metrics(r),
        })
    return _dedupe(wiersze, ("date", "profile_id", "target_id"))


def _rows_search_term(pid: int, ad_product: str, raw: list[dict[str, Any]]):
    return _dedupe(
        [
            {
                "date": _date(r.get("date")),
                "profile_id": pid,
                "campaign_id": _s(r.get("campaignId")),
                "ad_group_id": _s(r.get("adGroupId")),
                "target_id": _s(r.get("keywordId")),
                "search_term": (r.get("searchTerm") or "")[:500],
                "target_text": r.get("keywordText"),
                "match_type": r.get("matchType"),
                "ad_product": ad_product,
                **_metrics(r),
            }
            for r in raw
            if r.get("searchTerm") and _date(r.get("date"))
        ],
        ("date", "profile_id", "campaign_id", "target_id", "search_term"),
    )


def _rows_product(pid: int, ad_product: str, raw: list[dict[str, Any]]):
    return _dedupe(
        [
            {
                "date": _date(r.get("date")),
                "profile_id": pid,
                "campaign_id": _s(r.get("campaignId")),
                "ad_group_id": _s(r.get("adGroupId")),
                "asin": _s(r.get("promotedAsin")),
                "sku": _s(r.get("promotedSku")),
                "ad_product": ad_product,
                **_metrics(r),
            }
            for r in raw
            if _date(r.get("date"))
        ],
        ("date", "profile_id", "campaign_id", "ad_group_id", "sku", "asin"),
    )


RODZAJE = {
    "campaign": (CampaignDaily, _rows_campaign),
    "targeting": (TargetDaily, _rows_targeting),
    "search_term": (SearchTermDaily, _rows_search_term),
    "product": (ProductDaily, _rows_product),
}


def sync_reports(
    profile_id: int, ad_product: str, start: dt.date, end: dt.date,
    kinds: list[str] | None = None,
) -> dict[str, int]:
    """Pobiera i zapisuje raporty jednego typu produktu reklamowego.

    `ad_product` to "SB" albo "SD" - decyduje o zestawie raportow i o tym,
    jakim znacznikiem oznaczamy wiersze. Zapis kasuje okno **tylko w obrebie
    tego typu**, wiec dane SP zostaja nietkniete.
    """
    specs = reports.SB_SPECS if ad_product == "SB" else reports.SD_SPECS
    do_pobrania = [k for k in (kinds or specs.keys()) if k in specs]

    counts: dict[str, int] = {}
    with AdsClient(profile_id=profile_id) as client:
        for kind in do_pobrania:
            try:
                surowe = reports.fetch_window(client, kind, start, end, specs=specs)
            except Exception as exc:  # noqa: BLE001 - jeden raport nie blokuje reszty
                log.warning("%s raport %s: %s", ad_product, kind, str(exc)[:200])
                counts[kind] = -1
                continue

            model, builder = RODZAJE[kind]
            wiersze = builder(profile_id, ad_product, surowe)
            with session_scope() as session:
                counts[kind] = _replace_range(
                    session, model, profile_id, start, end, wiersze,
                    ad_product=ad_product,
                )
            log.info("%s %s: %s wierszy", ad_product, kind, counts[kind])

    return counts

"""Pobieranie encji Sponsored Display.

Tak jak SB, SD trafia do wspólnych tabel z kolumną `ad_product`. Archiwizacja
działa wyłącznie w obrębie własnego typu — inaczej sync jednego produktu
kasowałby pozostałe.

SD ma dwie rzeczy, których SP nie zna, i obie zapisujemy:

* **Taktykę** (`T00020` remarketing wyświetleń, `T00030` targetowanie
  odbiorców). To dwa różne mechanizmy zakupu i mierzenie ich jedną miarą
  wprowadza w błąd, więc trzymamy ją przy kampanii.
* **Typ kreacji** grupy reklam (`IMAGE`, `VIDEO`).

Obie lądują w `raw` oraz — tam gdzie pasują — w polach, które już istnieją:
taktyka w `targeting_type` kampanii, bo to najbliższy odpowiednik „w jaki
sposób dobierani są odbiorcy".
"""
from __future__ import annotations

import logging
from typing import Any

from ..ads.client import AdsClient
from ..ads import sd_entities as sd
from ..db import session_scope
from ..models import AdGroup, Campaign, NegativeTarget, ProductAd, Target
from .sync import _date, _f, _mark_archived, _upsert

log = logging.getLogger(__name__)

AD_PRODUCT = "SD"


def _wyrazenie_tekst(rekord: dict[str, Any]) -> str | None:
    """Czytelna postac wykluczenia albo targetu, np. 'asinCategorySameAs <kategoria>'."""
    czesci = []
    for e in rekord.get("resolvedExpression") or rekord.get("expression") or []:
        if isinstance(e, dict):
            czesci.append(" ".join(str(x) for x in (e.get("type"), e.get("value")) if x))
    return " + ".join(czesci) or None


def _kampanie(pid: int, dane: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "campaign_id": str(c["campaignId"]),
            "profile_id": pid,
            "ad_product": AD_PRODUCT,
            "portfolio_id": int(c["portfolioId"]) if c.get("portfolioId") else None,
            "name": c.get("name"),
            "state": sd.normalizuj_stan(c),
            # Taktyka to najblizszy odpowiednik "jak dobierani sa odbiorcy".
            "targeting_type": c.get("tactic"),
            "budget_amount": _f(c.get("budget")),
            "budget_type": (c.get("budgetType") or "").upper() or None,
            "bidding_strategy": (c.get("costType") or "").upper() or None,
            "placement_bidding": None,
            "start_date": _date(c.get("startDate")),
            "end_date": _date(c.get("endDate")),
            "raw": c,
        }
        for c in dane
    ]


def _grupy(pid: int, dane: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ad_group_id": str(g["adGroupId"]),
            "campaign_id": str(g["campaignId"]),
            "profile_id": pid,
            "ad_product": AD_PRODUCT,
            "name": g.get("name"),
            "state": sd.normalizuj_stan(g),
            "default_bid": _f(g.get("defaultBid")) or None,
            "raw": g,
        }
        for g in dane
    ]


def _targety(pid: int, dane: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "target_id": str(t["targetId"]),
            "ad_group_id": str(t.get("adGroupId") or ""),
            "campaign_id": str(t.get("campaignId") or ""),
            "profile_id": pid,
            "ad_product": AD_PRODUCT,
            "expression_type": (t.get("expressionType") or "").upper() or None,
            "expression": t.get("expression"),
            "resolved_expression": _wyrazenie_tekst(t),
            "state": sd.normalizuj_stan(t),
            "bid": _f(t.get("bid")) or None,
            "raw": t,
        }
        for t in dane
        if t.get("targetId")
    ]


def _reklamy(pid: int, dane: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ad_id": str(a["adId"]),
            "ad_group_id": str(a.get("adGroupId") or ""),
            "campaign_id": str(a.get("campaignId") or ""),
            "profile_id": pid,
            "ad_product": AD_PRODUCT,
            "asin": a.get("asin"),
            "sku": a.get("sku"),
            "state": sd.normalizuj_stan(a),
        }
        for a in dane
        if a.get("adId")
    ]


def _wykluczenia(pid: int, dane: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "target_id": str(t["targetId"]),
            "ad_group_id": str(t.get("adGroupId") or "") or None,
            "campaign_id": str(t.get("campaignId") or ""),
            "profile_id": pid,
            "level": "AD_GROUP",
            "expression_type": (t.get("expressionType") or "").upper() or None,
            "value": _wyrazenie_tekst(t),
            "state": sd.normalizuj_stan(t),
            "raw": t,
        }
        for t in dane
        if t.get("targetId")
    ]


def sync_entities(profile_id: int) -> dict[str, int]:
    """Pobiera i zapisuje kampanie, grupy, targety, reklamy i wykluczenia SD."""
    counts: dict[str, int] = {}

    with AdsClient(profile_id=profile_id) as client:
        kampanie = sd.list_campaigns(client)
        grupy = sd.list_ad_groups(client)
        targety = sd.list_targets(client)
        reklamy = sd.list_product_ads(client)
        wykluczenia = sd.list_negative_targets(client)

    with session_scope() as session:
        for model, pk, wiersze, nazwa in (
            (Campaign, "campaign_id", _kampanie(profile_id, kampanie), "campaigns"),
            (AdGroup, "ad_group_id", _grupy(profile_id, grupy), "ad_groups"),
            (Target, "target_id", _targety(profile_id, targety), "targets"),
            (ProductAd, "ad_id", _reklamy(profile_id, reklamy), "product_ads"),
        ):
            counts[nazwa] = _upsert(session, model, wiersze, pk)
            counts[f"{nazwa}_archived"] = _mark_archived(
                session, model, profile_id, {r[pk] for r in wiersze}, pk,
                ad_product=AD_PRODUCT,
            )

        # Wykluczenia nie maja kolumny ad_product, wiec ich nie archiwizujemy
        # po typie - skasowalibysmy wykluczenia SP.
        counts["negative_targets"] = _upsert(
            session, NegativeTarget, _wykluczenia(profile_id, wykluczenia), "target_id"
        )

    log.info("SD encje (profil %s): %s", profile_id, counts)
    return counts

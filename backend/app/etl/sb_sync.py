"""Pobieranie encji Sponsored Brands.

SB trafia do **tych samych tabel** co Sponsored Products, rozróżniane kolumną
`ad_product`. Schemat był na to przygotowany od początku, a wspólna tabela
oznacza, że portfolia, wykluczenia i widoki łączone działają dla obu typów bez
dublowania kodu.

Trzy rzeczy, które SB robi inaczej i które trzeba było uzgodnić przy zapisie:

* **Budżet jest liczbą**, nie obiektem `{budget, budgetType}` jak w SP.
* **Modyfikatory placementów** siedzą pod `bidding.bidAdjustmentsByPlacement`,
  a ich nazwy (`TOP_OF_SEARCH`, `HOME`, `OTHER`, `DETAIL_PAGE`) nie pokrywają
  się z SP. Zapisujemy je bez tłumaczenia — mapowanie ich na placementy SP
  byłoby fałszem, bo to inne miejsca.
* **Typ dopasowania** SB podaje małymi literami. Normalizujemy, żeby ta sama
  fraza nie wyglądała na dwa różne dopasowania zależnie od typu kampanii.

Grupy reklam SB nie mają stawki domyślnej — stawka siedzi wyłącznie przy
keywordzie. Zostawiamy `default_bid` puste, zamiast wpisywać zero, które
udawałoby ustawioną wartość.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ads.client import AdsClient
from ..ads import sb_entities as sb
from ..db import session_scope
from ..models import AdGroup, Campaign, Keyword, Target
from .sync import _date, _f, _mark_archived, _upsert

log = logging.getLogger(__name__)

AD_PRODUCT = "SB"


def _kampanie(pid: int, dane: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "campaign_id": str(c["campaignId"]),
            "profile_id": pid,
            "ad_product": AD_PRODUCT,
            "portfolio_id": int(c["portfolioId"]) if c.get("portfolioId") else None,
            "name": c.get("name"),
            "state": c.get("state"),
            # SB nie ma AUTO/MANUAL w rozumieniu SP. `smartDefault` mowi, czy
            # Amazon dobiera ustawienia sam - to najblizszy odpowiednik.
            "targeting_type": (c.get("smartDefault") or [None])[0],
            "budget_amount": _f(c.get("budget")),
            "budget_type": c.get("budgetType"),
            "bidding_strategy": (c.get("bidding") or {}).get("bidOptimizationStrategy"),
            "placement_bidding": sb.placement_bidding(c),
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
            "state": g.get("state"),
            # Brak stawki domyslnej w SB - puste, nie zero.
            "default_bid": None,
            "raw": g,
        }
        for g in dane
    ]


def _keywordy(pid: int, dane: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "keyword_id": str(k["keywordId"]),
            "ad_group_id": str(k.get("adGroupId") or ""),
            "campaign_id": str(k.get("campaignId") or ""),
            "profile_id": pid,
            "ad_product": AD_PRODUCT,
            "keyword_text": k.get("keywordText"),
            "match_type": sb.match_type(k),
            "state": (k.get("state") or "").upper() or None,
            "bid": _f(k.get("bid")) or None,
            "raw": k,
        }
        for k in dane
        if k.get("keywordId")
    ]


def _targety(pid: int, dane: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "target_id": str(t["targetId"]),
            "ad_group_id": str(t.get("adGroupId") or ""),
            "campaign_id": str(t.get("campaignId") or ""),
            "profile_id": pid,
            "ad_product": AD_PRODUCT,
            "expression_type": t.get("expressionType"),
            "expression": t.get("expression"),
            "state": (t.get("state") or "").upper() or None,
            "bid": _f(t.get("bid")) or None,
            "raw": t,
        }
        for t in dane
        if t.get("targetId")
    ]


def sync_entities(profile_id: int) -> dict[str, int]:
    """Pobiera i zapisuje kampanie, grupy, keywordy i targety SB."""
    counts: dict[str, int] = {}

    with AdsClient(profile_id=profile_id) as client:
        kampanie = sb.list_campaigns(client)
        grupy = sb.list_ad_groups(client)
        keywordy = sb.list_keywords(client)
        targety = sb.list_targets(client)

    with session_scope() as session:
        for model, pk, wiersze, nazwa in (
            (Campaign, "campaign_id", _kampanie(profile_id, kampanie), "campaigns"),
            (AdGroup, "ad_group_id", _grupy(profile_id, grupy), "ad_groups"),
            (Keyword, "keyword_id", _keywordy(profile_id, keywordy), "keywords"),
            (Target, "target_id", _targety(profile_id, targety), "targets"),
        ):
            counts[nazwa] = _upsert(session, model, wiersze, pk)
            # Archiwizacja **tylko w obrebie SB** - inaczej skasowalibysmy
            # wszystko, co nalezy do Sponsored Products.
            counts[f"{nazwa}_archived"] = _mark_archived(
                session, model, profile_id, {r[pk] for r in wiersze}, pk,
                ad_product=AD_PRODUCT,
            )

    log.info("SB encje (profil %s): %s", profile_id, counts)
    return counts

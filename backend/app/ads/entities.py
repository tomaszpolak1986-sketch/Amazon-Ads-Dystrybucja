"""Operacje na encjach Sponsored Products (v3): odczyt i zapis.

Odczyt uzywa endpointow /list z paginacja nextToken.
Zapis (PUT/POST) przyjmuje partie rekordow i zwraca rozbicie success/error,
ktore warstwa wyzej zapisuje do change_log.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import Any

from .client import SP_MEDIA, AdsClient

log = logging.getLogger(__name__)

BATCH_SIZE = 500

# klucz media-type -> (sciezka bazowa, klucz kolekcji w JSON)
RESOURCES: dict[str, tuple[str, str]] = {
    "campaign": ("/sp/campaigns", "campaigns"),
    "adGroup": ("/sp/adGroups", "adGroups"),
    "keyword": ("/sp/keywords", "keywords"),
    "negativeKeyword": ("/sp/negativeKeywords", "negativeKeywords"),
    "campaignNegativeKeyword": ("/sp/campaignNegativeKeywords", "campaignNegativeKeywords"),
    "target": ("/sp/targets", "targetingClauses"),
    "negativeTarget": ("/sp/negativeTargets", "negativeTargetingClauses"),
    "productAd": ("/sp/productAds", "productAds"),
}


def _chunks(items: list[Any], size: int = BATCH_SIZE) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------------------- #
# Odczyt
# --------------------------------------------------------------------------- #


def list_resource(
    client: AdsClient,
    resource: str,
    *,
    state_filter: list[str] | None = None,
    extra_filters: dict[str, Any] | None = None,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Pobiera wszystkie rekordy danego zasobu dla profilu klienta."""
    path, key = RESOURCES[resource]
    media = SP_MEDIA[resource]

    body: dict[str, Any] = dict(extra_filters or {})
    if state_filter:
        body["stateFilter"] = {"include": state_filter}

    rows = list(
        client.paginate(
            f"{path}/list", media_type=media, result_key=key, body=body, page_size=page_size
        )
    )
    log.info("Pobrano %s rekordow zasobu %s (profil %s)", len(rows), resource, client.profile_id)
    return rows


PORTFOLIO_MEDIA = "application/vnd.spportfolio.v3+json"


def list_portfolios(client: AdsClient) -> list[dict[str, Any]]:
    """Portfolia = grupy kampanii widoczne w panelu Amazona.

    Endpoint /v2/portfolios oraz /v2/portfolios/extended zostaly wycofane
    (zwracaja 404 Method Not Found). Aktualny jest POST /portfolios/list.
    """
    data = client.post(
        "/portfolios/list", json={}, accept=PORTFOLIO_MEDIA, content_type=PORTFOLIO_MEDIA
    ) or {}
    return data.get("portfolios", [])


# --------------------------------------------------------------------------- #
# Zapis
# --------------------------------------------------------------------------- #


def _write(
    client: AdsClient,
    resource: str,
    records: list[dict[str, Any]],
    method: str,
) -> dict[str, list[dict[str, Any]]]:
    """Wysyla partie rekordow, scalajac wyniki success/error ze wszystkich partii."""
    path, key = RESOURCES[resource]
    media = SP_MEDIA[resource]
    result: dict[str, list[dict[str, Any]]] = {"success": [], "error": []}

    for batch in _chunks(records):
        resp = client.request(
            method, path, json={key: batch}, accept=media, content_type=media
        ) or {}
        payload = resp.get(key, resp)
        if isinstance(payload, dict):
            result["success"].extend(payload.get("success") or [])
            result["error"].extend(payload.get("error") or [])
        elif isinstance(payload, list):
            result["success"].extend(payload)

    if result["error"]:
        # Sama liczba bledow nic nie mowi - bez tresci nie da sie odroznic
        # duplikatu od zlego dopasowania. Pierwszy blad wystarczy do diagnozy.
        log.warning(
            "%s %s: %s bledow, pierwszy: %s",
            method, resource, len(result["error"]), str(result["error"][0])[:400],
        )
    return result


def update(client: AdsClient, resource: str, records: list[dict[str, Any]]):
    return _write(client, resource, records, "PUT")


def create(client: AdsClient, resource: str, records: list[dict[str, Any]]):
    return _write(client, resource, records, "POST")


# --------------------------------------------------------------------------- #
# Skroty na najczestsze operacje
# --------------------------------------------------------------------------- #


def set_keyword_bids(client: AdsClient, pairs: Iterable[tuple[str, float]]):
    """[(keyword_id, nowy_bid), ...]"""
    records = [{"keywordId": str(kid), "bid": round(float(bid), 2)} for kid, bid in pairs]
    return update(client, "keyword", records)


def set_target_bids(client: AdsClient, pairs: Iterable[tuple[str, float]]):
    records = [{"targetId": str(tid), "bid": round(float(bid), 2)} for tid, bid in pairs]
    return update(client, "target", records)


def set_keyword_state(client: AdsClient, keyword_ids: Iterable[str], state: str):
    records = [{"keywordId": str(k), "state": state.upper()} for k in keyword_ids]
    return update(client, "keyword", records)


def set_target_state(client: AdsClient, target_ids: Iterable[str], state: str):
    records = [{"targetId": str(t), "state": state.upper()} for t in target_ids]
    return update(client, "target", records)


def set_ad_group_state(client: AdsClient, ad_group_ids: Iterable[str], state: str):
    records = [{"adGroupId": str(g), "state": state.upper()} for g in ad_group_ids]
    return update(client, "adGroup", records)


def set_campaign_state(client: AdsClient, campaign_ids: Iterable[str], state: str):
    records = [{"campaignId": str(c), "state": state.upper()} for c in campaign_ids]
    return update(client, "campaign", records)


def set_campaign_budgets(client: AdsClient, pairs: Iterable[tuple[str, float]],
                         budget_type: str = "DAILY"):
    records = [
        {
            "campaignId": str(cid),
            "budget": {"budget": round(float(amount), 2), "budgetType": budget_type},
        }
        for cid, amount in pairs
    ]
    return update(client, "campaign", records)


def add_negative_keywords(
    client: AdsClient,
    items: Iterable[dict[str, Any]],
    level: str = "AD_GROUP",
):
    """items: {campaignId, adGroupId (dla AD_GROUP), keywordText, matchType}

    matchType dla negatywow: NEGATIVE_EXACT albo NEGATIVE_PHRASE.
    """
    resource = "negativeKeyword" if level.upper() == "AD_GROUP" else "campaignNegativeKeyword"
    records = []
    for it in items:
        rec = {
            "campaignId": str(it["campaignId"]),
            "keywordText": it["keywordText"],
            "matchType": it.get("matchType", "NEGATIVE_EXACT"),
            "state": "ENABLED",
        }
        if resource == "negativeKeyword":
            rec["adGroupId"] = str(it["adGroupId"])
        records.append(rec)
    return create(client, resource, records)


def set_negative_keyword_state(
    client: AdsClient,
    keyword_ids: Iterable[str],
    state: str = "ARCHIVED",
    level: str = "AD_GROUP",
):
    """Wykluczenia sie nie edytuje - archiwizuje sie je i dodaje nowe.

    Amazon nie ma operacji zmiany tekstu ani dopasowania istniejacego
    wykluczenia. Archiwizacja jest nieodwracalna: zarchiwizowanego wykluczenia
    nie da sie wznowic, trzeba dodac je od nowa.
    """
    resource = "negativeKeyword" if level.upper() == "AD_GROUP" else "campaignNegativeKeyword"
    records = [{"keywordId": str(k), "state": state.upper()} for k in keyword_ids]
    return update(client, resource, records)


def set_negative_target_state(
    client: AdsClient, target_ids: Iterable[str], state: str = "ARCHIVED",
):
    records = [{"targetId": str(t), "state": state.upper()} for t in target_ids]
    return update(client, "negativeTarget", records)


def add_keywords(client: AdsClient, items: Iterable[dict[str, Any]]):
    """items: {campaignId, adGroupId, keywordText, matchType, bid}"""
    records = [
        {
            "campaignId": str(it["campaignId"]),
            "adGroupId": str(it["adGroupId"]),
            "keywordText": it["keywordText"],
            "matchType": it.get("matchType", "EXACT"),
            "state": it.get("state", "ENABLED"),
            **({"bid": round(float(it["bid"]), 2)} if it.get("bid") is not None else {}),
        }
        for it in items
    ]
    return create(client, "keyword", records)


def set_product_ad_state(client: AdsClient, ad_ids: Iterable[str], state: str):
    """Wstrzymuje albo wznawia reklamy konkretnych produktow.

    Produktu nie da sie "wykluczyc" z kampanii - Amazon nie ma takiej operacji.
    Wstrzymuje sie jego reklame w danej grupie, co daje ten sam efekt:
    przestaje sie wyswietlac, a historia zostaje.
    """
    records = [{"adId": str(a), "state": state.upper()} for a in ad_ids]
    return update(client, "productAd", records)


def set_placement_modifiers(client: AdsClient, items: Iterable[dict[str, Any]]):
    """Ustawia mnozniki stawek per placement.

    items: [{campaignId, strategy, placements: [{placement, percentage}]}]

    Placementy SP: PLACEMENT_TOP (gora wynikow), PLACEMENT_PRODUCT_PAGE (karta
    produktu), PLACEMENT_REST_OF_SEARCH. Amazon przyjmuje calkowite procenty 0-900;
    modyfikator zastepuje poprzedni, wiec zawsze wysylamy pelna liste placementow
    kampanii, nie sam zmieniany element.
    """
    records = [
        {
            "campaignId": str(it["campaignId"]),
            "dynamicBidding": {
                "strategy": it.get("strategy") or "LEGACY_FOR_SALES",
                "placementBidding": [
                    {
                        "placement": p["placement"],
                        "percentage": max(0, min(900, int(round(float(p["percentage"]))))),
                    }
                    for p in it["placements"]
                ],
            },
        }
        for it in items
    ]
    return update(client, "campaign", records)


def wyrazenie_negatywu(it: dict[str, Any]) -> list[dict[str, str]]:
    """Wyrazenie wykluczenia produktowego: konkretny ASIN albo cala marka.

    Amazon przyjmuje oba rodzaje tym samym zasobem, roznica siedzi wylacznie
    w typie wyrazenia. Marka jest identyfikowana liczbowym `brandId` z
    uszczegolowien kategorii - nazwa w zadaniu nie dziala.

    `expression` podane wprost ma pierwszenstwo: plany zapisane przed tym
    rozroznieniem niosa samo `asin` i musza dalej dzialac.
    """
    gotowe = it.get("expression")
    if gotowe:
        return gotowe
    if it.get("brandId"):
        return [{"type": "ASIN_BRAND_SAME_AS", "value": str(it["brandId"])}]
    return [{"type": "ASIN_SAME_AS", "value": str(it["asin"]).upper()}]


def add_negative_targets(client: AdsClient, items: Iterable[dict[str, Any]]):
    """Wyklucza ASIN-y albo cale marki z targetowania produktowego.

    items: [{campaignId, adGroupId, asin}] albo [{campaignId, adGroupId, brandId}]

    Jedno i drugie istnieje tylko na poziomie grupy reklam - Amazon nie ma
    wykluczen produktowych dla calej kampanii.
    """
    records = [
        {
            "campaignId": str(it["campaignId"]),
            "adGroupId": str(it["adGroupId"]),
            "expression": wyrazenie_negatywu(it),
            "state": "ENABLED",
        }
        for it in items
    ]
    return create(client, "negativeTarget", records)


# --------------------------------------------------------------------------- #
# Tworzenie kampanii - kaskada encji
# --------------------------------------------------------------------------- #


def _created_id(result: dict[str, Any], key: str) -> str | None:
    """Identyfikator pierwszej utworzonej encji albo None, gdy nic nie powstalo."""
    for item in result.get("success") or []:
        value = item.get(key) or (item.get("campaign") or {}).get(key)
        if value:
            return str(value)
    return None


def create_campaign(client: AdsClient, spec: dict[str, Any]) -> dict[str, Any]:
    """Tworzy sama kampanie. Zawsze wstrzymana - status ustawia sie na koncu."""
    record: dict[str, Any] = {
        "name": spec["name"],
        "targetingType": spec["targeting_type"],
        "state": "PAUSED",
        "budget": {
            "budget": round(float(spec["daily_budget"]), 2),
            "budgetType": "DAILY",
        },
        "dynamicBidding": {"strategy": spec.get("bidding_strategy", "LEGACY_FOR_SALES")},
    }
    if spec.get("start_date"):
        record["startDate"] = spec["start_date"]
    if spec.get("end_date"):
        record["endDate"] = spec["end_date"]
    if spec.get("portfolio_id"):
        record["portfolioId"] = str(spec["portfolio_id"])
    if spec.get("placement_bidding"):
        record["dynamicBidding"]["placementBidding"] = spec["placement_bidding"]

    return create(client, "campaign", [record])


def create_ad_group(
    client: AdsClient, campaign_id: str, name: str, default_bid: float
) -> dict[str, Any]:
    return create(client, "adGroup", [{
        "campaignId": str(campaign_id),
        "name": name,
        "defaultBid": round(float(default_bid), 2),
        "state": "ENABLED",
    }])


def create_product_ads(
    client: AdsClient, campaign_id: str, ad_group_id: str,
    skus: Iterable[str] = (), asins: Iterable[str] = (),
) -> dict[str, Any]:
    """Reklamy produktowe. SKU dla wlasnych ofert, ASIN gdy sprzedajemy cudze."""
    records = [
        {"campaignId": str(campaign_id), "adGroupId": str(ad_group_id),
         "sku": sku, "state": "ENABLED"}
        for sku in skus
    ] + [
        {"campaignId": str(campaign_id), "adGroupId": str(ad_group_id),
         "asin": asin, "state": "ENABLED"}
        for asin in asins
    ]
    return create(client, "productAd", records) if records else {"success": [], "error": []}


def create_auto_targets(
    client: AdsClient, campaign_id: str, ad_group_id: str,
    groups: Iterable[str], bid: float | None = None,
) -> dict[str, Any]:
    """Grupy targetowania automatycznego (close-match, loose-match, itd.)."""
    records = [
        {
            "campaignId": str(campaign_id),
            "adGroupId": str(ad_group_id),
            "expressionType": "PREDEFINED",
            "expression": [{"type": group}],
            "state": "ENABLED",
            **({"bid": round(float(bid), 2)} if bid is not None else {}),
        }
        for group in groups
    ]
    return create(client, "target", records) if records else {"success": [], "error": []}


def create_product_targets(
    client: AdsClient, campaign_id: str, ad_group_id: str,
    items: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Targetowanie produktowe: {expression: [...], bid}."""
    records = [
        {
            "campaignId": str(campaign_id),
            "adGroupId": str(ad_group_id),
            "expressionType": "MANUAL",
            "expression": it["expression"],
            "state": "ENABLED",
            **({"bid": round(float(it["bid"]), 2)} if it.get("bid") is not None else {}),
        }
        for it in items
    ]
    return create(client, "target", records) if records else {"success": [], "error": []}

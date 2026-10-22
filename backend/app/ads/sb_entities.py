"""Encje Sponsored Brands.

Osobny moduł, bo SB to praktycznie inne API niż Sponsored Products — i to
nie kosmetycznie:

* **Kampanie i grupy** siedzą pod `/sb/v4/...` i wymagają własnych nagłówków
  `vnd.sb*resource.v4+json`. Keywordy natomiast **nie mają wersji v4** —
  `/sb/v4/keywords/list` zwraca 403, działa dopiero stary `GET /sb/keywords`.
  To nie pomyłka w ścieżce, tylko stan API: sprawdzone na koncie.
* **Targetowanie produktowe** jest pod `POST /sb/targets/list`, nie `/sb/v4`.
* **Placementy nazywają się inaczej.** SP ma `PLACEMENT_TOP`, SB ma
  `TOP_OF_SEARCH`, `HOME`, `OTHER`, `DETAIL_PAGE`. Nie wolno ich mieszać ani
  mapować jeden do jednego — to inne miejsca.
* **Budżet jest liczbą**, nie obiektem jak w SP.

Moduł tylko czyta. Zapis do SB dokładamy dopiero, gdy będzie czego pilnować —
teraz chodzi o to, żeby 14 aktywnych kampanii przestało być niewidoczne.
"""
from __future__ import annotations

import logging
from typing import Any

from .client import AdsClient

log = logging.getLogger(__name__)

# Nagłówki wersjonowane per zasób - SB nie ma jednego wspólnego.
MEDIA_KAMPANIA = "application/vnd.sbcampaignresource.v4+json"
MEDIA_GRUPA = "application/vnd.sbadgroupresource.v4+json"

# Ile rekordów na stronę. SB przyjmuje maxResults, nie pageSize.
STRONA = 100

# Placementy SB - inne niż w SP, dlatego mapa jest tutaj, nie współdzielona.
PLACEMENTY = {
    "TOP_OF_SEARCH": "Góra wyników",
    "DETAIL_PAGE": "Karta produktu",
    "HOME": "Strona główna",
    "OTHER": "Pozostałe",
}


def _strony(
    client: AdsClient, sciezka: str, media: str, klucz: str
) -> list[dict[str, Any]]:
    """Przechodzi po stronach listy SB.

    SB stronicuje tokenem `nextToken` w ciele, a nie parametrem w URL-u —
    dlatego nie da się użyć wspólnego `client.paginate` z modułu SP.
    """
    out: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        body: dict[str, Any] = {"maxResults": STRONA}
        if token:
            body["nextToken"] = token
        resp = client.request(
            "POST", sciezka, json=body, accept=media, content_type=media
        ) or {}
        out.extend(resp.get(klucz) or [])
        token = resp.get("nextToken")
        if not token:
            return out


def list_campaigns(client: AdsClient) -> list[dict[str, Any]]:
    kampanie = _strony(client, "/sb/v4/campaigns/list", MEDIA_KAMPANIA, "campaigns")
    log.info("SB: %s kampanii (profil %s)", len(kampanie), client.profile_id)
    return kampanie


def list_ad_groups(client: AdsClient) -> list[dict[str, Any]]:
    grupy = _strony(client, "/sb/v4/adGroups/list", MEDIA_GRUPA, "adGroups")
    log.info("SB: %s grup reklam (profil %s)", len(grupy), client.profile_id)
    return grupy


def list_keywords(client: AdsClient) -> list[dict[str, Any]]:
    """Keywordy SB. Tylko stary endpoint - v4 dla nich nie istnieje."""
    out = client.request("GET", "/sb/keywords") or []
    log.info("SB: %s keywordow (profil %s)", len(out), client.profile_id)
    return out


def list_targets(client: AdsClient) -> list[dict[str, Any]]:
    """Targetowanie produktowe SB. Na kontach bez SB Product Targeting puste."""
    resp = client.request("POST", "/sb/targets/list", json={"maxResults": STRONA}) or {}
    out = resp.get("targets") or resp.get("targetingClauses") or []
    log.info("SB: %s targetow (profil %s)", len(out), client.profile_id)
    return out


def placement_bidding(kampania: dict[str, Any]) -> list[dict[str, Any]]:
    """Modyfikatory placementów z konfiguracji kampanii SB.

    Klucz nazywa sie `bidAdjustmentsByPlacement`, a nie `placementBidding`
    jak w SP - kolejna rzecz, ktorej nie da sie wspoldzielic miedzy typami.
    """
    return ((kampania.get("bidding") or {}).get("bidAdjustmentsByPlacement")) or []


def match_type(keyword: dict[str, Any]) -> str | None:
    """Typ dopasowania w tej samej postaci co w SP.

    SB zwraca `broad`, `phrase`, `exact` malymi literami, SP - wielkimi.
    Bez ujednolicenia ta sama fraza wygladalaby na dwa rozne dopasowania
    w zaleznosci od typu kampanii.
    """
    mt = keyword.get("matchType")
    return mt.upper() if isinstance(mt, str) else None


def set_keyword_bids(client: AdsClient, records: list[dict[str, Any]]):
    """Zmiana stawek keywordow SB - stary zasob, tak jak przy odczycie.

    records: [{keywordId, campaignId, adGroupId, bid}]

    Stary interfejs SB wymaga w kazdym rekordzie takze `campaignId`
    i `adGroupId` - bez nich odpowiada KEYWORD_MISSING_AD_GROUP_ID, mimo ze
    sam identyfikator keyworda jest jednoznaczny. SP v3 tego nie potrzebuje,
    stad roznica w ksztalcie wobec `entities.set_keyword_bids`.
    """
    body = [
        {
            "keywordId": int(r["keywordId"]),
            "campaignId": int(r["campaignId"]),
            "adGroupId": int(r["adGroupId"]),
            "bid": round(float(r["bid"]), 2),
        }
        for r in records
    ]
    return na_ksztalt_v3(client.request("PUT", "/sb/keywords", json=body), "keywordId")


def _v4_write(client: AdsClient, sciezka: str, media: str, klucz: str, records: list[dict]):
    """PUT na zasob SB v4. Odpowiedz ma ksztalt SP v3, ale zagniezdzony pod
    nazwa zasobu: {"campaigns": {"success": [...], "error": [...]}}."""
    resp = client.request(
        "PUT", sciezka, json={klucz: records}, accept=media, content_type=media
    ) or {}
    wynik = resp.get(klucz) if isinstance(resp.get(klucz), dict) else resp
    return {"success": wynik.get("success") or [], "error": wynik.get("error") or []}


def set_campaign_state(client: AdsClient, campaign_ids: list[str], state: str):
    records = [{"campaignId": str(c), "state": str(state).upper()} for c in campaign_ids]
    return _v4_write(client, "/sb/v4/campaigns", MEDIA_KAMPANIA, "campaigns", records)


def set_campaign_budgets(client: AdsClient, pairs: list[tuple[str, float]]):
    records = [{"campaignId": str(c), "budget": round(float(b), 2)} for c, b in pairs]
    return _v4_write(client, "/sb/v4/campaigns", MEDIA_KAMPANIA, "campaigns", records)


def set_ad_group_state(client: AdsClient, ad_group_ids: list[str], state: str):
    records = [{"adGroupId": str(g), "state": str(state).upper()} for g in ad_group_ids]
    return _v4_write(client, "/sb/v4/adGroups", MEDIA_GRUPA, "adGroups", records)


def set_keyword_state(client: AdsClient, records: list[dict[str, Any]], state: str):
    """records: [{keywordId, campaignId, adGroupId}] - stary interfejs wymaga
    calej trojki, a stany przyjmuje malymi literami."""
    body = [
        {"keywordId": int(r["keywordId"]), "campaignId": int(r["campaignId"]),
         "adGroupId": int(r["adGroupId"]), "state": str(state).lower()}
        for r in records
    ]
    return na_ksztalt_v3(client.request("PUT", "/sb/keywords", json=body), "keywordId")


def set_target_state(client: AdsClient, records: list[dict[str, Any]], state: str):
    """records: [{targetId, campaignId, adGroupId}]"""
    body = [
        {"targetId": int(r["targetId"]), "campaignId": int(r["campaignId"]),
         "adGroupId": int(r["adGroupId"]), "state": str(state).lower()}
        for r in records
    ]
    return na_ksztalt_v3(client.request("PUT", "/sb/targets", json=body), "targetId")


# Negatywy w SB uzywaja innej pisowni dopasowania niz SP.
_NEGATYW_SB = {"NEGATIVE_EXACT": "negativeExact", "NEGATIVE_PHRASE": "negativePhrase"}


def add_negative_keywords(client: AdsClient, items: list[dict[str, Any]]):
    """Wykluczenia fraz w SB - tylko na poziomie grupy reklam.

    items: [{campaignId, adGroupId, keywordText, matchType}]

    Ten sam ksztalt wejscia co `entities.add_negative_keywords`, zeby
    wykonawca planu mogl rozdzielic pozycje po typie kampanii i nic wiecej.
    Wysylka SB na `/sp/negativeKeywords` konczy sie "parent program type
    must be Sponsored Products".
    """
    body = [
        {
            "campaignId": int(it["campaignId"]),
            "adGroupId": int(it["adGroupId"]),
            "keywordText": it["keywordText"],
            "matchType": _NEGATYW_SB.get(
                str(it.get("matchType", "NEGATIVE_EXACT")).upper(), "negativeExact"
            ),
        }
        for it in items
    ]
    return na_ksztalt_v3(client.request("POST", "/sb/negativeKeywords", json=body), "keywordId")


def na_ksztalt_v3(odpowiedz, id_field: str) -> dict[str, list]:
    """Odpowiedz starego API (lista) -> ksztalt, ktorego uzywa reszta panelu.

    SP w wersji v3 zwraca `{"success": [...], "error": [...]}`. SB i SD siedza
    na starszym interfejsie i oddaja **plaska liste** z polem `code`:
    `[{"code": "SUCCESS", "targetId": 123}]`. Bez tego przelozenia wspolny
    kod wynikow wywracal sie na `list.get`.
    """
    if isinstance(odpowiedz, dict):
        return odpowiedz
    sukces, bledy = [], []
    for i, pozycja in enumerate(odpowiedz or []):
        if not isinstance(pozycja, dict):
            continue
        # Stary interfejs odpowiada w kolejnosci wysylki, ale bez `index`.
        # Dopisujemy go, zeby przy czesciowym powodzeniu dalo sie powiedziec,
        # ktora pozycja przeszla - tak jak robi to SP v3.
        pozycja.setdefault("index", i)
        if str(pozycja.get("code", "")).upper() == "SUCCESS":
            sukces.append(pozycja)
        else:
            # Zachowujemy oryginalna tresc - to ona mowi, czemu Amazon odmowil.
            bledy.append(pozycja)
    return {"success": sukces, "error": bledy}

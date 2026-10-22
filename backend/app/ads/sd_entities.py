"""Encje Sponsored Display.

SD ma najprostsze API z trzech typów — wszystko przez `GET /sd/...`, bez
wersjonowanych nagłówków i bez tokenów stronicowania. Ale różni się od SP
i SB w miejscach, które łatwo przeoczyć:

* **Stany są małymi literami** (`enabled`, `paused`, `archived`), a w SP
  i SB wielkimi. Bez normalizacji ta sama kampania wyglądałaby na aktywną
  w jednym widoku i nieznaną w drugim.
* **Identyfikatory przychodzą jako liczby**, nie napisy — reszta panelu
  operuje na napisach, więc rzutujemy przy zapisie.
* **`expressionType`** to `manual` albo `auto`, znowu małymi literami,
  a typy w `resolvedExpression` mają postać `asinCategorySameAs`.
* **Stronicowanie po `startIndex`/`count`.** Bez tego lista reklam urywa się
  na 5000 pozycji i wygląda na kompletną.

Taktyka kampanii (`tactic`) to rzecz, której SP nie ma wcale:
`T00020` to remarketing wyświetleń, `T00030` — targetowanie odbiorców.
To dwa różne mechanizmy i porównywanie ich jedną miarą wprowadza w błąd.
"""
from __future__ import annotations

import logging
from typing import Any

from .client import AdsClient

log = logging.getLogger(__name__)

STRONA = 500

TAKTYKI = {
    "T00020": "remarketing wyświetleń",
    "T00030": "targetowanie odbiorców",
}


def _wszystko(client: AdsClient, sciezka: str) -> list[dict[str, Any]]:
    """Pobiera cały zasób, przechodząc po stronach.

    SD nie zwraca ani łącznej liczby, ani tokenu - koniec poznajemy po
    stronie krótszej niż żądana. Dlatego pętla jest tu jawna, zamiast
    wspólnego `paginate` z SP.
    """
    out: list[dict[str, Any]] = []
    start = 0
    while True:
        strona = client.request(
            "GET", sciezka, params={"startIndex": start, "count": STRONA}
        ) or []
        out.extend(strona)
        if len(strona) < STRONA:
            return out
        start += STRONA


def _stan(wartosc: Any) -> str | None:
    """Stan w tej samej postaci co w SP i SB."""
    return wartosc.upper() if isinstance(wartosc, str) else None


def list_campaigns(client: AdsClient) -> list[dict[str, Any]]:
    dane = _wszystko(client, "/sd/campaigns")
    log.info("SD: %s kampanii (profil %s)", len(dane), client.profile_id)
    return dane


def list_ad_groups(client: AdsClient) -> list[dict[str, Any]]:
    dane = _wszystko(client, "/sd/adGroups")
    log.info("SD: %s grup reklam (profil %s)", len(dane), client.profile_id)
    return dane


def list_targets(client: AdsClient) -> list[dict[str, Any]]:
    dane = _wszystko(client, "/sd/targets")
    log.info("SD: %s targetow (profil %s)", len(dane), client.profile_id)
    return dane


def list_product_ads(client: AdsClient) -> list[dict[str, Any]]:
    dane = _wszystko(client, "/sd/productAds")
    log.info("SD: %s reklam produktow (profil %s)", len(dane), client.profile_id)
    return dane


def list_negative_targets(client: AdsClient) -> list[dict[str, Any]]:
    dane = _wszystko(client, "/sd/negativeTargets")
    log.info("SD: %s wykluczonych targetow (profil %s)", len(dane), client.profile_id)
    return dane


def normalizuj_stan(rekord: dict[str, Any]) -> str | None:
    return _stan(rekord.get("state"))


def set_target_bids(client: AdsClient, pairs: list[tuple[str, float]]):
    """Zmiana stawek targetow SD.

    SD ma wlasny zasob - wyslanie tych identyfikatorow na `/sp/targets`
    skonczyloby sie odrzuceniem, bo Amazon nie zna ich w Sponsored Products.
    """
    records = [{"targetId": int(tid), "bid": round(float(bid), 2)} for tid, bid in pairs]
    return na_ksztalt_v3(client.request("PUT", "/sd/targets", json=records), "targetId")


def _stan_dla_api(state: str) -> str:
    """SD przyjmuje stany malymi literami (`enabled`, `paused`, `archived`);
    reszta panelu operuje wielkimi, jak SP v3."""
    return str(state).lower()


def set_target_state(client: AdsClient, target_ids: list[str], state: str):
    records = [{"targetId": int(t), "state": _stan_dla_api(state)} for t in target_ids]
    return na_ksztalt_v3(client.request("PUT", "/sd/targets", json=records), "targetId")


def set_product_ad_state(client: AdsClient, ad_ids: list[str], state: str):
    records = [{"adId": int(a), "state": _stan_dla_api(state)} for a in ad_ids]
    return na_ksztalt_v3(client.request("PUT", "/sd/productAds", json=records), "adId")


def set_ad_group_state(client: AdsClient, ad_group_ids: list[str], state: str):
    records = [{"adGroupId": int(g), "state": _stan_dla_api(state)} for g in ad_group_ids]
    return na_ksztalt_v3(client.request("PUT", "/sd/adGroups", json=records), "adGroupId")


def set_campaign_state(client: AdsClient, campaign_ids: list[str], state: str):
    records = [{"campaignId": int(c), "state": _stan_dla_api(state)} for c in campaign_ids]
    return na_ksztalt_v3(client.request("PUT", "/sd/campaigns", json=records), "campaignId")


def set_campaign_budgets(client: AdsClient, pairs: list[tuple[str, float]]):
    records = [{"campaignId": int(c), "budget": round(float(b), 2)} for c, b in pairs]
    return na_ksztalt_v3(client.request("PUT", "/sd/campaigns", json=records), "campaignId")


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
        # Stary interfejs odpowiada w kolejnosci wysylki, bez `index`
        pozycja.setdefault("index", i)
        if str(pozycja.get("code", "")).upper() == "SUCCESS":
            sukces.append(pozycja)
        else:
            # Zachowujemy oryginalna tresc - to ona mowi, czemu Amazon odmowil.
            bledy.append(pozycja)
    return {"success": sukces, "error": bledy}

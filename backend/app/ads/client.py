"""Klient HTTP Amazon Ads API.

Obsluguje:
  * naglowki LWA + ClientId + Scope (profileId),
  * wersjonowane content-typy v3 (application/vnd.spCampaign.v3+json itd.),
  * ponawianie przy 429 / 5xx z wykladniczym backoffem i Retry-After,
  * odswiezenie tokena przy 401,
  * paginacje nextToken dla endpointow /list.
"""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from typing import Any

import httpx

from ..config import settings
from .auth import TokenProvider, token_provider

log = logging.getLogger(__name__)

MAX_RETRIES = 6
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Bledy transportowe (zerwany DNS, ubity socket) dostaja osobna, hojniejsza pule.
# Odpowiedz 429 czy 503 to sygnal od Amazona i szesc prob wystarczy, ale brak
# odpowiedzi w ogole oznacza zwykle chwilowa awarie lacza po naszej stronie -
# przy kilkugodzinnym pobieraniu raportow taniej jest poczekac kilka minut niz
# stracic okno danych.
NETWORK_RETRIES = 12
NETWORK_BACKOFF_CAP = 120.0

# Wersjonowane typy MIME dla zasobow Sponsored Products v3
SP_MEDIA = {
    "campaign": "application/vnd.spCampaign.v3+json",
    "adGroup": "application/vnd.spAdGroup.v3+json",
    "keyword": "application/vnd.spKeyword.v3+json",
    "negativeKeyword": "application/vnd.spNegativeKeyword.v3+json",
    "campaignNegativeKeyword": "application/vnd.spCampaignNegativeKeyword.v3+json",
    "target": "application/vnd.spTargetingClause.v3+json",
    "negativeTarget": "application/vnd.spNegativeTargetingClause.v3+json",
    "productAd": "application/vnd.spProductAd.v3+json",
}


class AdsApiError(RuntimeError):
    def __init__(self, status: int, message: str, url: str = "", payload: Any = None) -> None:
        super().__init__(f"HTTP {status} @ {url}: {message}")
        self.status = status
        self.message = message
        self.url = url
        self.payload = payload


class AdsClient:
    """Cienki wrapper na Ads API zwiazany z jednym profilem (lub bez profilu dla /v2/profiles)."""

    def __init__(
        self,
        profile_id: int | str | None = None,
        provider: TokenProvider | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.profile_id = str(profile_id) if profile_id is not None else None
        self.provider = provider or token_provider
        self.base_url = (base_url or settings.ads_base_url).rstrip("/")
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    # ------------------------------------------------------------------ #

    def _headers(self, accept: str | None, content_type: str | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.provider.get()}",
            "Amazon-Advertising-API-ClientId": self.provider.client_id,
        }
        if self.profile_id:
            headers["Amazon-Advertising-API-Scope"] = self.profile_id
        if accept:
            headers["Accept"] = accept
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _sleep_for(attempt: int, resp: httpx.Response | None) -> float:
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(60.0, float(retry_after))
                except ValueError:
                    pass
        return min(60.0, (2**attempt) + random.uniform(0, 1.0))

    @staticmethod
    def _sleep_for_network(attempt: int) -> float:
        return min(NETWORK_BACKOFF_CAP, (2**attempt) + random.uniform(0, 1.0))

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        accept: str | None = None,
        content_type: str | None = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        last_error: Exception | None = None

        attempt = 0        # proby po odpowiedzi HTTP z RETRY_STATUSES
        net_attempt = 0    # proby po bledzie transportowym
        while attempt < MAX_RETRIES and net_attempt < NETWORK_RETRIES:
            try:
                resp = self._http.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=self._headers(accept, content_type),
                )
            except httpx.RequestError as exc:  # sieciowy blad - ponow
                last_error = exc
                wait = self._sleep_for_network(net_attempt)
                net_attempt += 1
                if net_attempt >= NETWORK_RETRIES:
                    break
                log.warning(
                    "Blad sieci (%s/%s) przy %s: %s - ponawiam za %.0fs",
                    net_attempt, NETWORK_RETRIES, url, str(exc)[:120], wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                log.info("401 - odswiezam access token i ponawiam")
                self.provider.invalidate()
                self.provider.get(force=True)
                last_error = AdsApiError(401, resp.text[:500], url)
                attempt += 1
                continue

            if resp.status_code in RETRY_STATUSES:
                wait = self._sleep_for(attempt, resp)
                log.warning("HTTP %s @ %s - ponawiam za %.1fs", resp.status_code, url, wait)
                last_error = AdsApiError(resp.status_code, resp.text[:500], url)
                attempt += 1
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                # Blad koncowy obcinamy pozno: Amazon wypisuje w nim liste
                # dozwolonych kolumn raportu, a przy 1000 znakach urywala sie
                # w polowie i diagnostyka byla bezuzyteczna.
                raise AdsApiError(resp.status_code, resp.text[:6000], url)

            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.content

        raise AdsApiError(
            getattr(last_error, "status", 0),
            f"Wyczerpano proby ({attempt} po odpowiedzi HTTP, "
            f"{net_attempt} po bledzie sieci): {last_error}",
            url,
        )

    # ------------------------------------------------------------------ #

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> Any:
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)

    # ------------------------------------------------------------------ #

    def paginate(
        self,
        path: str,
        *,
        media_type: str,
        result_key: str,
        body: dict[str, Any] | None = None,
        page_size: int = 500,
        max_pages: int = 500,
    ) -> Iterator[dict[str, Any]]:
        """Iteruje po endpointach /list v3 uzywajacych nextToken."""
        payload = dict(body or {})
        payload["maxResults"] = page_size
        pages = 0

        while pages < max_pages:
            data = self.post(path, json=payload, accept=media_type, content_type=media_type)
            pages += 1
            items = (data or {}).get(result_key) or []
            yield from items

            next_token = (data or {}).get("nextToken")
            if not next_token or not items:
                return
            payload["nextToken"] = next_token

        log.warning("Przerwano paginacje %s po %s stronach", path, max_pages)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AdsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def list_profiles(provider: TokenProvider | None = None) -> list[dict[str, Any]]:
    """Profile sa poza scope - osobny wywolanie bez naglowka Scope."""
    with AdsClient(profile_id=None, provider=provider) as client:
        return client.get("/v2/profiles") or []

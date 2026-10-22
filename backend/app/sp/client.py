"""Klient Selling Partner API.

Uwierzytelnianie jest takie samo jak w Ads API - token LWA w naglowku - ale
aplikacja jest inna, wiec i klucze inne. Od pazdziernika 2023 Amazon nie wymaga
juz konta AWS ani podpisow SigV4, wiec caly klient sprowadza sie do odswiezania
tokenu i ponawiania przy limitach.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from ..config import LWA_TOKEN_URL, settings

log = logging.getLogger(__name__)

# SP-API tnie przepustowosc ostro i sygnalizuje to kodem 429. Ponawiamy
# z rosnaca przerwa; przy pobieraniu raportow nie zalezy nam na sekundach.
MAX_RETRIES = 8
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Token zyje godzine; odswiezamy wczesniej, zeby nie trafic na wygasniecie
# w polowie dlugiego pobierania.
TOKEN_MARGIN_S = 300


class SpApiError(RuntimeError):
    def __init__(self, status: int, body: str, path: str) -> None:
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f"HTTP {status} @ {path}: {body[:400]}")

    @property
    def is_forbidden(self) -> bool:
        """403 oznacza tu zwykle brak roli, nie zly token."""
        return self.status == 403


class SpClient:
    def __init__(self, timeout: float = 60.0) -> None:
        if not settings.sp_configured:
            raise RuntimeError(
                "Brak konfiguracji SP-API. Uzupelnij SP_CLIENT_ID, SP_CLIENT_SECRET "
                "i SP_REFRESH_TOKEN w pliku .env."
            )
        self.base_url = settings.sp_base_url
        self._http = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._expires_at: float = 0.0

    def __enter__(self) -> SpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ #

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - TOKEN_MARGIN_S:
            return self._token

        resp = self._http.post(
            LWA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": settings.sp_refresh_token,
                "client_id": settings.sp_client_id,
                "client_secret": settings.sp_client_secret,
            },
        )
        if resp.status_code != 200:
            raise SpApiError(resp.status_code, resp.text, "LWA")

        dane = resp.json()
        self._token = dane["access_token"]
        self._expires_at = time.time() + float(dane.get("expires_in", 3600))
        return self._token

    @staticmethod
    def _sleep_for(attempt: int, status: int | None = None) -> float:
        # 429 z Reports API to limit liczony w minutach, nie w sekundach -
        # krotkie ponawianie tylko zuzywa proby, nie zmieniajac wyniku.
        cap = 300.0 if status == 429 else 90.0
        return min(cap, (2**attempt) + random.uniform(0, 1.0))

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        ostatni: Exception | None = None

        for attempt in range(MAX_RETRIES):
            headers = {
                "x-amz-access-token": self._access_token(),
                "Accept": "application/json",
            }
            if json is not None:
                headers["Content-Type"] = "application/json"

            try:
                resp = self._http.request(method, url, json=json, params=params, headers=headers)
            except httpx.RequestError as exc:
                ostatni = exc
                time.sleep(self._sleep_for(attempt))
                continue

            if resp.status_code in RETRY_STATUSES:
                czekaj = self._sleep_for(attempt, resp.status_code)
                log.warning("HTTP %s @ %s - ponawiam za %.0fs", resp.status_code, path, czekaj)
                ostatni = SpApiError(resp.status_code, resp.text, path)
                time.sleep(czekaj)
                continue

            if resp.status_code >= 400:
                raise SpApiError(resp.status_code, resp.text, path)

            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.content

        raise SpApiError(
            getattr(ostatni, "status", 0),
            f"Wyczerpano {MAX_RETRIES} prob: {ostatni}",
            path,
        )

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)


def check_access() -> dict[str, Any]:
    """Sprawdza, czy klucze dzialaja i czy jest rola do raportu sprzedazy.

    Zwraca opis stanu zamiast rzucac wyjatkiem - to jest funkcja diagnostyczna,
    ktora ma powiedziec, co dokladnie nie gra.
    """
    out: dict[str, Any] = {"configured": settings.sp_configured}
    if not settings.sp_configured:
        out["problem"] = "Brak kluczy SP-API w .env"
        return out

    try:
        with SpClient() as c:
            rynki = c.get("/sellers/v1/marketplaceParticipations")
            out["auth"] = True
            out["marketplaces"] = len(rynki.get("payload", []) or [])

            try:
                c.get(
                    "/reports/2021-06-30/reports",
                    params={
                        "reportTypes": "GET_SALES_AND_TRAFFIC_REPORT",
                        "marketplaceIds": "A1PA6795UKMFR9",
                    },
                )
                out["sales_report"] = True
            except SpApiError as exc:
                out["sales_report"] = False
                out["problem"] = (
                    "Brak roli Brand Analytics przy aplikacji"
                    if exc.is_forbidden
                    else str(exc)[:200]
                )
    except SpApiError as exc:
        out["auth"] = False
        out["problem"] = str(exc)[:200]

    return out

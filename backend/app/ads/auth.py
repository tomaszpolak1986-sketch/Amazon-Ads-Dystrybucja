"""Autoryzacja Login with Amazon (LWA) - wymiana refresh_token na access_token.

Access token zyje 60 minut. Trzymamy go w pamieci procesu i odswiezamy
z 5-minutowym zapasem, zeby nie zlapac 401 w srodku dlugiego syncu.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from ..config import LWA_TOKEN_URL, settings

log = logging.getLogger(__name__)

_REFRESH_MARGIN_S = 300


class LwaAuthError(RuntimeError):
    pass


class TokenProvider:
    """Thread-safe cache access tokena."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        self.client_id = client_id or settings.ads_client_id
        self.client_secret = client_secret or settings.ads_client_secret
        self.refresh_token = refresh_token or settings.ads_refresh_token
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> tuple[str, int]:
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise LwaAuthError(
                "Brak danych LWA. Uzupelnij ADS_CLIENT_ID / ADS_CLIENT_SECRET / "
                "ADS_REFRESH_TOKEN w pliku .env"
            )

        resp = httpx.post(
            LWA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise LwaAuthError(
                f"LWA odrzucilo refresh tokena (HTTP {resp.status_code}): {resp.text[:500]}"
            )
        payload = resp.json()
        return payload["access_token"], int(payload.get("expires_in", 3600))

    def get(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or time.time() >= self._expires_at:
                token, expires_in = self._fetch()
                self._token = token
                self._expires_at = time.time() + max(60, expires_in - _REFRESH_MARGIN_S)
                log.debug("Odswiezono access token, wazny %ss", expires_in)
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0


token_provider = TokenProvider()

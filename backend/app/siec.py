"""Pobieranie gotowych plikow raportow z ponawianiem.

Zapytania do API Amazona maja wlasne ponawianie w klientach, ale sam plik
raportu (adres S3 podpisany na kilka minut) byl pobierany jednym strzalem.
To ostatni krok po 5-20 minutach czekania, az Amazon wygeneruje raport -
jedna czkawka DNS w tym momencie wyrzucala caly raport. 20.11.2026 na laptopie
z niestabilnym DNS przepadly tak raporty DE i sprzedaz za caly miesiac.
"""
from __future__ import annotations

import logging
import random
import time

import httpx

log = logging.getLogger(__name__)

PROBY = 6


def pobierz_plik(url: str, timeout: float = 180.0) -> bytes:
    """GET bez autoryzacji z ponawianiem bledow sieci i 5xx."""
    for proba in range(1, PROBY + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as http:
                resp = http.get(url)
            if resp.status_code >= 500 and proba < PROBY:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.content
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            if proba >= PROBY:
                raise
            czekaj = min(30.0, 2 ** proba + random.uniform(0, 1))
            log.warning(
                "Pobieranie pliku raportu: %s (proba %s/%s) - ponawiam za %.0fs",
                str(exc)[:120], proba, PROBY, czekaj,
            )
            time.sleep(czekaj)
    raise RuntimeError("nieosiagalne")  # pragma: no cover

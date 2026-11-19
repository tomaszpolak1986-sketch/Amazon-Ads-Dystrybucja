"""Dzienne odświeżanie danych pomocniczych: oferty, katalog, słownik kategorii.

Wcześniej pobierało się je wyłącznie poleceniami w terminalu (`cli.py
listings`, `catalog`) albo przyciskiem w panelu. U klienta nikt ich nie
uruchamiał, więc wyszukiwarka produktów i kategorii była pusta, produkty nie
miały tytułów ani miniatur - choć klucze i uprawnienia były na miejscu.

Wyłącznie ODCZYT z Amazona. Raz na dobę wystarcza: asortyment, ceny i
słownik kategorii zmieniają się wolno, a katalog dociąga tylko produkty bez
świeżego wpisu.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..config import PROJECT_ROOT, settings
from . import sync_lock

log = logging.getLogger(__name__)

# Znacznik ostatniego udanego przebiegu - w pliku, żeby przeżył restart.
# Harmonogram woła nas co kilka godzin; jeśli przebieg był niedawno albo inny
# trwa (blokada), nic się nie dzieje i następna okazja przyjdzie sama.
_ZNACZNIK = PROJECT_ROOT / "data" / "slowniki-ostatnio.txt"
CO_ILE = dt.timedelta(hours=20)


def _ostatnio() -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(_ZNACZNIK.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def odswiez(wymus: bool = False) -> dict[str, Any] | None:
    """Odświeża oferty, katalog i słownik kategorii; None, gdy nic nie robiono."""
    ostatnio = _ostatnio()
    if not wymus and ostatnio and dt.datetime.now() - ostatnio < CO_ILE:
        return None
    if not settings.is_configured:
        return None

    from . import catalog, labels, listings

    kroki = [("kategorie", labels.sync_all_labels)]
    if settings.sp_configured:
        kroki = [
            ("oferty", lambda: listings.sync_all()),
            ("katalog", lambda: catalog.sync_all(only_stale=True)),
            *kroki,
        ]

    wynik: dict[str, Any] = {}
    try:
        with sync_lock.przebieg("oferty, katalog i kategorie"):
            for nazwa, krok in kroki:
                try:
                    wynik[nazwa] = krok()
                except Exception as exc:  # noqa: BLE001 - jeden krok nie zabiera reszty
                    log.exception("Słowniki: %s nie powiodło się", nazwa)
                    wynik[nazwa] = {"error": str(exc)[:200]}
    except sync_lock.Zajete as exc:
        log.info("Słowniki pominięte - trwa inne pobieranie: %s", exc)
        return None

    try:
        _ZNACZNIK.parent.mkdir(parents=True, exist_ok=True)
        _ZNACZNIK.write_text(dt.datetime.now().isoformat(), encoding="utf-8")
    except OSError:
        log.warning("Nie udało się zapisać znacznika słowników")
    log.info("Słowniki odświeżone: %s", {k: (v if isinstance(v, dict) and "error" in v else "ok") for k, v in wynik.items()})
    return wynik

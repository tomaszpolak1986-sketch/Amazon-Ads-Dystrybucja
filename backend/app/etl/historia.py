"""Stopniowe dociaganie historii: reklamy, sprzedaz calkowita i FBA wstecz.

Pierwsze pobranie bierze 30 dni, bo musi skonczyc sie w rozsadnym czasie.
Wczesniej na tym sie konczylo - w panelu klienta pazdziernik mial 12 z 31
dni i nic tego nie uzupelnialo. Amazon trzyma raporty reklamowe okolo 90 dni,
wiec reszta jest do wziecia, tylko nikt po nia nie siegal.

Zasada: raz na przebieg JEDNA paczka (do 30 dni) na rynek, od najnowszej
brakujacej w tyl, az do GLEBOKOSC dni. Sprzedaz calkowita i FBA ida w tym
samym zakresie co reklamy, zeby TACOS liczyl sie na kompletnych danych.

Wylacznie ODCZYT z Amazona. Blokada jest na rynek, nie na caly przebieg -
inaczej trzymalibysmy ja godzinami i wstrzymywali zwykle odswiezanie.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from sqlalchemy import func, select

from ..config import PROJECT_ROOT, settings
from ..db import session_scope
from ..models import Campaign, CampaignDaily, FulfillmentDaily, Profile, TotalSalesDaily
from . import sync_lock

log = logging.getLogger(__name__)

GLEBOKOSC = 90          # dni wstecz od wczoraj - mniej wiecej tyle trzyma Amazon
PACZKA = 30             # raport reklamowy obejmuje maksymalnie ~31 dni
MAKS_PORAZEK = 3        # po tylu calkowicie nieudanych probach rynek konczy historie

# Rynek, ktorego starszych danych Amazon juz nie wyda, nie moze byc ponawiany
# w nieskonczonosc. Licznik porazek i granica w pliku, zeby przetrwaly restart.
_STAN = PROJECT_ROOT / "data" / "historia-stan.json"


def _wczytaj() -> dict[str, Any]:
    try:
        return json.loads(_STAN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _zapisz(stan: dict[str, Any]) -> None:
    try:
        _STAN.parent.mkdir(parents=True, exist_ok=True)
        _STAN.write_text(json.dumps(stan, indent=1), encoding="utf-8")
    except OSError:
        log.warning("Historia: nie udalo sie zapisac stanu")


def _paczka(najstarsza: dt.date | None, cel: dt.date) -> tuple[dt.date, dt.date] | None:
    """Kolejna paczka wstecz od najstarszego dnia w bazie; None = komplet."""
    if najstarsza is None or najstarsza <= cel:
        return None
    koniec = najstarsza - dt.timedelta(days=1)
    return max(cel, koniec - dt.timedelta(days=PACZKA - 1)), koniec


def krok() -> dict[str, Any] | None:
    """Jedna paczka historii na rynek. None, gdy nie ma nic do zrobienia."""
    if not settings.is_configured:
        return None

    from . import orders_sync, sales_sync, sync

    wczoraj = dt.date.today() - dt.timedelta(days=1)
    cel = wczoraj - dt.timedelta(days=GLEBOKOSC - 1)
    ukryte = settings.skip_market_list
    stan = _wczytaj()
    wynik: dict[str, Any] = {}

    with session_scope() as s:
        profile = [
            (pid, kod or str(pid))
            for pid, kod in s.execute(select(Profile.profile_id, Profile.country_code)).all()
            if (kod or "").upper() not in ukryte
        ]
        z_kampaniami = set(s.execute(select(Campaign.profile_id).distinct()).scalars())
        reklamy_od = dict(s.execute(
            select(CampaignDaily.profile_id, func.min(CampaignDaily.date))
            .group_by(CampaignDaily.profile_id)
        ).all())
        sprzedaz_od = dict(s.execute(
            select(TotalSalesDaily.profile_id, func.min(TotalSalesDaily.date))
            .group_by(TotalSalesDaily.profile_id)
        ).all())
        fba_od = s.execute(select(func.min(FulfillmentDaily.date))).scalar()

    def _od(klucz: str, z_bazy: dt.date | None) -> dt.date | None:
        """Najstarszy dzien: z bazy albo dokad juz siegnelismy. Paczka bez
        wierszy (rynek nie mial wtedy reklam) nie przesuwa minimum w bazie -
        bez zapamietania pobieralibysmy to samo okno co 3 h w nieskonczonosc."""
        zapisane = stan.get("od:" + klucz)
        if zapisane:
            d = dt.date.fromisoformat(zapisane)
            return min(d, z_bazy) if z_bazy else d
        return z_bazy

    def _udane(klucz: str, okno: tuple[dt.date, dt.date]) -> None:
        stan.pop(klucz, None)
        stan["od:" + klucz] = okno[0].isoformat()

    def _porazka(klucz: str, bledy: Any) -> None:
        stan[klucz] = int(stan.get(klucz, 0)) + 1
        if stan[klucz] >= MAKS_PORAZEK:
            log.warning("Historia %s: %s nieudanych prob - koncze (%s)", klucz, stan[klucz], bledy)

    for pid, kod in profile:
        # --- reklamy ---
        klucz = f"reklamy:{pid}"
        okno = _paczka(_od(klucz, reklamy_od.get(pid)), cel) if pid in z_kampaniami else None
        if okno and int(stan.get(klucz, 0)) < MAKS_PORAZEK:
            try:
                with sync_lock.przebieg(f"historia reklam ({kod})"):
                    w = sync.sync_reports(pid, *okno)
                udane = {k: v for k, v in w.items() if k != "errors"}
                if w.get("errors") and not any(udane.values()):
                    _porazka(klucz, w["errors"])
                else:
                    _udane(klucz, okno)
                wynik[f"reklamy {kod}"] = f"{okno[0]}..{okno[1]}"
            except sync_lock.Zajete:
                pass
            except Exception as exc:  # noqa: BLE001 - jeden rynek nie zabiera reszty
                log.exception("Historia reklam %s nie powiodla sie", kod)
                _porazka(klucz, str(exc)[:200])

        # --- sprzedaz calkowita: ten sam zakres co reklamy ---
        if not settings.sp_configured:
            continue
        klucz = f"sprzedaz:{pid}"
        okno = _paczka(_od(klucz, sprzedaz_od.get(pid)), cel)
        if okno and int(stan.get(klucz, 0)) < MAKS_PORAZEK:
            try:
                with sync_lock.przebieg(f"historia sprzedazy ({kod})"):
                    sales_sync.sync_all(okno[0], okno[1], pid)
                _udane(klucz, okno)
                wynik[f"sprzedaz {kod}"] = f"{okno[0]}..{okno[1]}"
            except sync_lock.Zajete:
                pass
            except Exception as exc:  # noqa: BLE001
                log.exception("Historia sprzedazy %s nie powiodla sie", kod)
                _porazka(klucz, str(exc)[:200])

    # --- FBA: jeden raport zamowien na cale konto ---
    okno = _paczka(_od("fba", fba_od), cel) if settings.sp_configured else None
    if okno and int(stan.get("fba", 0)) < MAKS_PORAZEK:
        try:
            with sync_lock.przebieg("historia FBA"):
                w = orders_sync.sync_window(*okno)
            if isinstance(w, dict) and w.get("error"):
                _porazka("fba", w["error"])
            else:
                _udane("fba", okno)
            wynik["FBA"] = f"{okno[0]}..{okno[1]}"
        except sync_lock.Zajete:
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("Historia FBA nie powiodla sie")
            _porazka("fba", str(exc)[:200])

    _zapisz(stan)
    if wynik:
        log.info("Historia danych: %s", wynik)
    return wynik or None

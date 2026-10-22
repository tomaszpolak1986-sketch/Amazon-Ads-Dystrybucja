"""FastAPI - warstwa HTTP panelu."""
from __future__ import annotations

import datetime as dt
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from typing import Any, Literal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from . import auth, licencja
from ._wydanie import WERSJA, WYMAGA_LICENCJI
from .api import actions, anomalies, effectiveness, konto_amazon, queries
from .config import settings
from .db import get_session, init_db
from .etl import sync, sync_lock
from .rules import engine as rules_engine
from .etl import business_report, total_sales
from .rules import templates as rule_templates
from .models import Campaign, Rule, RuleRun, SyncRun
from .timeutil import iso_utc

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("adspanel")

def _scheduled_rules_tick() -> None:
    """Cykliczne uruchamianie regul.

    Scheduler NIE wprowadza zmian - kazda regula tworzy plan czekajacy na
    zatwierdzenie. Najgorsze, co moze zrobic zle skonfigurowana regula, to
    zapelnic kolejke propozycjami.
    """
    from .db import session_scope

    try:
        with session_scope() as session:
            result = rules_engine.run_due_rules(session)
        if result["ran"]:
            log.info("Scheduler: uruchomiono %s regul", result["ran"])
    except Exception:
        log.exception("Scheduler regul: przebieg nie powiodl sie")


def _scheduled_sb_sd_refresh() -> None:
    """Dobowe pobranie SB/SD - pod ta sama blokada, co reszta synchronizacji."""
    try:
        with sync_lock.przebieg("raporty SB/SD"):
            _sb_sd_refresh_body()
    except sync_lock.Zajete as exc:
        log.info("SB/SD pominiete: %s", exc)


def _sb_sd_refresh_body() -> None:
    """Dobowe pobranie raportow Sponsored Brands i Sponsored Display.

    Osobne zadanie, bo te raporty rzadza sie inaczej niz SP: generuja sie
    20-60 minut (zmierzone), wiec przy odswiezaniu co 6 godzin kolejne
    zlecenie trafialoby na jeszcze nieukonczone poprzednie. Raz na dobe, w
    godzinie o malym ruchu, jest tanio i wystarczajaco czesto - SB i SD nie
    zmieniaja sie z godziny na godzine.

    To wylacznie ODCZYT. Zasada "kazda zmiana wymaga zgody" dotyczy zapisu
    i pozostaje nienaruszona.
    """
    import datetime as dt

    from sqlalchemy import select

    from .db import session_scope
    from .etl import sb_sd_reports, sb_sync, sd_sync
    from .models import Profile

    if not settings.sb_sd_refresh_enabled:
        return

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=settings.sb_sd_refresh_days - 1)

    try:
        with session_scope() as session:
            rynki = [
                (pid, kod or str(pid))
                for pid, kod in session.execute(
                    select(Profile.profile_id, Profile.country_code)
                ).all()
                if (kod or "").upper() not in settings.skip_market_list
            ]
    except Exception:
        log.exception("SB/SD: nie udalo sie ustalic listy rynkow")
        return

    log.info("SB/SD: start dobowego pobrania, %s..%s", start, end)

    for pid, kod in rynki:
        # Encje najpierw - bez nich raporty nie maja do czego przypiac nazw.
        # Kazdy rynek osobno, zeby jeden bledny nie zabral pozostalych.
        for etykieta, krok in (
            ("SB encje", lambda p=pid: sb_sync.sync_entities(p)),
            ("SD encje", lambda p=pid: sd_sync.sync_entities(p)),
            ("SB raporty", lambda p=pid: sb_sd_reports.sync_reports(p, "SB", start, end)),
            ("SD raporty", lambda p=pid: sb_sd_reports.sync_reports(p, "SD", start, end)),
        ):
            try:
                wynik = krok()
                log.info("%s %s: %s", etykieta, kod, wynik)
            except Exception:
                log.exception("%s %s: nie powiodlo sie", etykieta, kod)

    log.info("SB/SD: dobowe pobranie zakonczone")


def _odswiez_kursy() -> None:
    """Kursy EBC do raportow zbiorczych. Blad sieci nie moze wywrocic panelu -
    bez kursu kwota zostaje nieprzeliczona, a nie znika."""
    try:
        from .etl import fx
        wynik = fx.odswiez()
        if wynik.get("stored"):
            log.info("Kursy walut: %s", wynik)
    except Exception:
        log.exception("Odswiezanie kursow walut nie powiodlo sie")


def _scheduled_data_refresh(od: dt.date | None = None) -> None:
    """Cykliczne dociaganie swiezych danych - jeden przebieg naraz.

    `od` przesuwa poczatek okna wstecz - uzywa tego nadrabianie, gdy znajdzie
    luke starsza niz zwykle okno.

    Gdy inny przebieg trwa (przycisk w panelu, nadrabianie po starcie),
    ten odpuszcza. Nastepna okazja przyjdzie sama: sprawdzenie swiezosci
    co godzine albo kolejny termin planowy.
    """
    try:
        with sync_lock.przebieg("odswiezanie planowe"):
            _data_refresh_body(od)
    except sync_lock.Zajete as exc:
        log.info("Odswiezanie pominiete: %s", exc)


def _data_refresh_body(od: dt.date | None = None) -> None:
    """Cykliczne dociaganie swiezych danych.

    To wylacznie ODCZYT - pobranie raportow niczego w Amazonie nie zmienia,
    wiec nie ma tu czego zatwierdzac. Zasada "kazda zmiana wymaga zgody"
    dotyczy zapisu i pozostaje nienaruszona.

    Bierzemy krotkie okno wstecz zamiast samego wczoraj, bo Amazon uzupelnia
    konwersje z opoznieniem: dzien sprzed trzech dni potrafi jeszcze urosnac.
    Zapis nadpisuje zakres, wiec powtarzanie go jest bezpieczne.

    Rynki idziemy po kolei i w osobnych transakcjach - jeden nieudany nie
    zabiera pozostalych.
    """
    import datetime as dt

    from sqlalchemy import func, select

    from .db import session_scope
    from .etl import sync as etl_sync
    from .models import Campaign, Profile

    if not settings.auto_refresh_enabled:
        return

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=settings.auto_refresh_days - 1)
    if od and od < start:
        # Luka starsza niz zwykle okno - siegamy po nia, ale nie dalej niz
        # retencja raportow Amazona (frazy znikaja po ~65 dniach).
        start = max(od, end - dt.timedelta(days=60))

    try:
        with session_scope() as session:
            rynki = [
                (pid, kod or str(pid))
                for pid, kod, ile in session.execute(
                    select(Profile.profile_id, Profile.country_code,
                           func.count(Campaign.campaign_id))
                    .join(Campaign, Campaign.profile_id == Profile.profile_id, isouter=True)
                    .group_by(Profile.profile_id, Profile.country_code)
                ).all()
                # `ile` odsiewa profile bez kampanii, skip_market_list rynki,
                # na ktorych swiadomie nie sprzedajemy.
                if ile and (kod or "").upper() not in settings.skip_market_list
            ]
    except Exception:
        log.exception("Odswiezanie: nie udalo sie ustalic listy rynkow")
        return

    log.info("Odswiezanie danych: %s rynkow, %s..%s",
             len(rynki), start, end)

    # Czas idzie na czekanie, az Amazon wygeneruje raport (~20 min), wiec dwa
    # rynki naraz skracaja przebieg o polowe, nie obciazajac nas dwukrotnie.
    from concurrent.futures import ThreadPoolExecutor

    def jeden(pid: int, kod: str) -> None:
        try:
            wynik = etl_sync.sync_reports(pid, start, end)
            bledy = wynik.get("errors") or {}
            if bledy:
                log.warning("Odswiezanie %s: czesciowo (%s)", kod, list(bledy))
        except Exception:
            log.exception("Odswiezanie %s nie powiodlo sie", kod)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda a: jeden(*a), rynki))

    # Sprzedaz calkowita tylko wtedy, gdy SP-API jest skonfigurowane
    if settings.sp_configured:
        try:
            from .etl import sales_sync
            sales_sync.sync_all(start, end)
        except Exception:
            log.exception("Odswiezanie sprzedazy calkowitej nie powiodlo sie")

        # Podzial na FBA i wysylke wlasna. Jeden raport na cale konto, nie per
        # rynek - raport zamowien zwraca wszystkie rynki naraz, wiec kosztuje
        # tyle co jedno zapytanie, a nie dziesiec.
        try:
            from .etl import orders_sync
            orders_sync.sync_window(start, end)
        except Exception:
            log.exception("Odswiezanie podzialu FBA nie powiodlo sie")

    log.info("Odswiezanie danych zakonczone")


def _startup_catchup() -> None:
    """Dociaga dane raz po starcie, ale tylko gdy sa nieaktualne.

    Sprawdzenie przed pobraniem jest tu istotne. Bez niego kazdy restart
    panelu - takze ten po drobnej zmianie w kodzie - zamawialby komplet
    raportow u Amazona, co trwa kilkadziesiat minut i niepotrzebnie zjada
    limity. Pobieramy wiec dopiero, gdy najswiezszy dzien w bazie jest
    starszy niz wczorajszy, czyli faktycznie czegos brakuje.

    Wczoraj, a nie dzis: raporty Amazona za biezacy dzien nie sa kompletne,
    wiec ich brak nie jest zalegloscia.
    """
    from sqlalchemy import func, select

    from .api import queries
    from .db import session_scope
    from .models import (
        CampaignDaily, FulfillmentDaily, PlacementDaily, ProductDaily,
        SearchTermDaily, TargetDaily, TotalSalesDaily,
    )

    try:
        with session_scope() as session:
            reklamy = session.execute(select(func.max(CampaignDaily.date))).scalar()
            sprzedaz = session.execute(select(func.max(TotalSalesDaily.date))).scalar()
            fba = session.execute(select(func.max(FulfillmentDaily.date))).scalar()
            # Osobno Sponsored Brands i Display: maja wlasny, dobowy przebieg,
            # a dotad nic nie sprawdzalo, czy w ogole doszedl. Na komputerze
            # wlaczanym po poludniu zadanie z 4:00 przepadalo i SB/SD po prostu
            # nie bylo w bazie - przy 430 EUR wydatku miesiecznie to nie drobiazg.
            sb_sd = session.execute(
                select(func.max(CampaignDaily.date))
                .where(CampaignDaily.ad_product.in_(("SB", "SD")))
            ).scalar()
            # Luki w srodku okna. Sama najnowsza data ich nie pokazuje, a to
            # wlasnie one przekrecaja sumy na pulpicie.
            #
            # Sprawdzamy KAZDA tabele faktow, nie tylko kampanie: raporty sa
            # osobne i potrafia zawiesc pojedynczo. Audyt pokazal dzien,
            # w ktorym kampanie mialy komplet siedmiu rynkow, a raport
            # produktow tylko trzy - patrzac na same kampanie nie byloby tego
            # widac, a zakladka Produkty pokazywalaby zanizone liczby.
            luki_reklam = _najstarsza_luka(session, (
                CampaignDaily, PlacementDaily, TargetDaily, SearchTermDaily, ProductDaily,
            ))
            luki_sprzedazy = _najstarsza_luka(session, (TotalSalesDaily, FulfillmentDaily))
    except Exception:
        log.exception("Nadrabianie po starcie: nie udalo sie sprawdzic swiezosci danych")
        return

    dzis = dt.date.today()
    # Kazde zrodlo ma wlasny prog, bo dojrzewaja w innym tempie. Raporty
    # reklamowe za wczoraj sa dostepne rano, raport biznesowy Seller Central
    # dochodzi dobe pozniej. Wspolny prog "wczoraj" uznawalby sprzedaz za
    # zalegla praktycznie zawsze i przy kazdym starcie zamawialby raporty,
    # ktorych po stronie Amazona jeszcze nie ma.
    prog_reklam = dzis - dt.timedelta(days=1)
    # Sprzedaz tez rozliczamy do wczoraj. Wczesniej prog wynosil dwa dni, bo
    # raport biznesowy dojrzewa dluzej i przy progu "wczoraj" panel probowal
    # pobrac dzien, ktorego Amazon jeszcze nie wystawil. To jednak nie szkodzi:
    # pobranie jest odczytem, nieudana proba nic nie psuje, a kolejne podejscie
    # (przy starcie albo co 6 h) dociagnie dzien, gdy tylko sie pojawi.
    # Lepiej sprobowac kilka razy za duzo niz pokazywac wczorajsze liczby bez
    # sprzedazy, bo wtedy TACOS i TROAS milcza akurat dla najswiezszego dnia.
    prog_sprzedazy = dzis - dt.timedelta(days=1)

    # Podzial FBA idzie z raportu zamowien, ktory publikuje sie szybciej niz
    # raport sprzedazy - stad prog jak przy reklamach, nie jak przy sprzedazy.
    prog_fba = dzis - dt.timedelta(days=1)

    # SB i SD dojrzewaja wolniej (raport generuje sie 20-60 min), wiec dwa dni
    # obsuwy to norma, nie zaleglosc.
    prog_sb_sd = dzis - dt.timedelta(days=2)

    brak_reklam = not reklamy or reklamy < prog_reklam
    brak_sprzedazy = not sprzedaz or sprzedaz < prog_sprzedazy
    brak_fba = not fba or fba < prog_fba
    brak_sb_sd = settings.sb_sd_refresh_enabled and (not sb_sd or sb_sd < prog_sb_sd)
    sa_luki = bool(luki_reklam["from"] or luki_sprzedazy["from"])

    if not (brak_reklam or brak_sprzedazy or brak_fba or brak_sb_sd or sa_luki):
        log.info(
            "Nadrabianie po starcie zbedne - reklamy do %s, sprzedaz do %s, FBA do %s, "
            "SB/SD do %s, bez luk",
            reklamy, sprzedaz, fba, sb_sd,
        )
        return

    log.info(
        "Nadrabianie po starcie: reklamy do %s (prog %s), sprzedaz do %s (prog %s), "
        "FBA do %s (prog %s), SB/SD do %s (prog %s)%s - pobieram",
        reklamy or "brak", prog_reklam, sprzedaz or "brak", prog_sprzedazy,
        fba or "brak", prog_fba, sb_sd or "brak", prog_sb_sd,
        _opis_luk(luki_reklam, luki_sprzedazy) if sa_luki else "",
    )

    # Okno pobrania siega najstarszej luki, nie tylko ostatnich kilku dni -
    # inaczej dziura w srodku zostaje tam na zawsze. Raport Amazona kosztuje
    # tyle samo zapytan niezaleznie od dlugosci okna, wiec poszerzenie go nie
    # obciaza limitu; rosnie tylko czas generowania.
    od_luki = min(
        [d for d in (luki_reklam["from"], luki_sprzedazy["from"]) if d],
        default=None,
    )
    _scheduled_data_refresh(od=od_luki)

    if brak_sb_sd:
        try:
            with sync_lock.przebieg("raporty SB/SD (nadrabianie)"):
                _sb_sd_refresh_body()
        except sync_lock.Zajete as exc:
            log.info("SB/SD po starcie pominiete: %s", exc)


def _najstarsza_luka(session, modele) -> dict:
    """Najstarsza luka wsrod kilku tabel - razem z nazwa tej, ktorej dotyczy."""
    from .api import queries

    najstarsza, skad, ile = None, None, 0
    for model in modele:
        luki = queries.luki_w_danych(session, model)
        if not luki["from"]:
            continue
        ile += len(luki["missing"]) + len(luki["partial"])
        if najstarsza is None or luki["from"] < najstarsza:
            najstarsza, skad = luki["from"], model.__tablename__
    return {"from": najstarsza, "table": skad, "days": ile}


def _opis_luk(luki_reklam: dict, luki_sprzedazy: dict) -> str:
    """Krotki opis dziur do logu - zeby bylo widac, co wywolalo pobranie."""
    czesci = []
    for nazwa, luki in (("reklamy", luki_reklam), ("sprzedaz", luki_sprzedazy)):
        if luki.get("days"):
            czesci.append(f"{nazwa}: {luki['days']} dni od {luki['from']} ({luki['table']})")
    return " | LUKI - " + ", ".join(czesci) if czesci else ""


def _stan_licencji() -> licencja.Stan:
    if not WYMAGA_LICENCJI:
        return licencja.Stan(True, "Wersja bez licencji.")
    return licencja.stan()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    log.info(
        # Ten sam opis co w /api/health - pelny adres niesie haslo do bazy,
        # a log trafia do dziennika, ktory czyta wiecej osob niz .env.
        "AdsPanel wystartowal (region=%s, db=%s)",
        settings.ads_region, _opis_bazy(),
    )

    if settings.auth_disabled:
        log.warning(
            "UWIERZYTELNIANIE WYLACZONE (AUTH_DISABLED=true). Dopuszczalne tylko "
            "przy pracy lokalnej - nie wystawiaj tak panelu do sieci."
        )
    elif not settings.auth_ready:
        log.warning(
            "Panel nie ma ustawionego hasla - logowanie odrzuci kazda probe. "
            "Ustaw je: python backend/scripts/cli.py setpassword"
        )

    stan_licencji = _stan_licencji()
    if not stan_licencji.ok:
        # API i tak odrzuci kazde zapytanie (guard), ale pobieranie w tle
        # chodziloby dalej i zuzywalo limity Amazona - wiec nie startujemy go.
        log.error("%s Panel zablokowany, harmonogram nie wystartuje.", stan_licencji.powod)
        yield
        return
    if WYMAGA_LICENCJI:
        log.info("Licencja: %s, wazna do %s", stan_licencji.klient, stan_licencji.wazna_do)

    scheduler = BackgroundScheduler(timezone=settings.tz)

    def _pilnuj_licencji() -> None:
        # Kontener chodzi miesiacami bez restartu - licencja wygasa w trakcie.
        if not _stan_licencji().ok:
            log.error("Licencja wygasla - wstrzymuje harmonogram.")
            scheduler.pause()

    scheduler.add_job(
        _pilnuj_licencji, "interval", hours=1,
        id="licencja", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _scheduled_rules_tick, "interval", minutes=30,
        id="rules", max_instances=1, coalesce=True,
    )

    if settings.auto_refresh_enabled:
        scheduler.add_job(
            _scheduled_data_refresh, "interval",
            hours=settings.auto_refresh_hours,
            id="data_refresh", replace_existing=True,
            max_instances=1, coalesce=True,
            # Bez next_run_time - wyzwalacz interwalowy i tak czeka pelny okres
            # przed pierwszym przebiegiem, wiec restart panelu nie ciagnie
            # raportow od razu. (next_run_time=None wstrzymalby zadanie na stale.)
        )
        log.info(
            "Odswiezanie danych wlaczone: co %sh, okno %s dni",
            settings.auto_refresh_hours, settings.auto_refresh_days,
        )

        # Nadrobienie zaleglosci po starcie. Wyzwalacz interwalowy odlicza pelny
        # okres OD URUCHOMIENIA, wiec na komputerze wylaczanym na noc panel
        # przez pierwsze auto_refresh_hours godzin pokazywal wczorajsze dane -
        # a to wlasnie rano czlowiek do niego zaglada. Zadanie jednorazowe
        # sprawdza swiezosc i pobiera tylko wtedy, gdy faktycznie czegos brakuje,
        # wiec zwykly restart panelu nie wywoluje niepotrzebnego ruchu.
        #
        # Opoznienie daje czas na wstanie reszty aplikacji i na polaczenie
        # z siecia, ktore po wybudzeniu laptopa bywa gotowe kilkadziesiat
        # sekund po samym systemie.
        scheduler.add_job(
            _startup_catchup, "date",
            run_date=dt.datetime.now() + dt.timedelta(seconds=90),
            id="startup_catchup", replace_existing=True,
        )

        # To samo sprawdzenie co godzine. Powod jest konkretny: raport sprzedazy
        # dojrzewa w ciagu dnia i wczorajszy dzien potrafi pojawic sie dopiero
        # po poludniu. Przy samym odswiezaniu co 6 godzin panel czekalby z nim
        # nawet pol dnia, mimo ze dane leza juz u Amazona.
        #
        # Sprawdzenie jest tanie - trzy zapytania o `max(date)` - i pobiera
        # wylacznie to, czego brakuje, wiec przy swiezych danych kosztuje tyle
        # co nic. `max_instances=1` pilnuje, zeby kolejny tik nie wszedl
        # w trwajace pobieranie, ktore potrafi zajac ~25 minut.
        scheduler.add_job(
            _startup_catchup, "interval", hours=1,
            id="freshness_tick", replace_existing=True,
            max_instances=1, coalesce=True,
        )

    # Kursy walut: raz po starcie i raz na dobe. Potrzebne tylko wtedy, gdy
    # konto ma rynki w roznych walutach - sam `zapewnij_kursy` to sprawdza
    # i przy jednej walucie konczy sie bez zadnego zapytania.
    scheduler.add_job(
        _odswiez_kursy, "date",
        run_date=dt.datetime.now() + dt.timedelta(seconds=20),
        id="fx_startup", replace_existing=True,
    )
    scheduler.add_job(
        _odswiez_kursy, "cron", hour=17, minute=30,
        id="fx_daily", replace_existing=True,
        max_instances=1, coalesce=True,
        # EBC publikuje okolo 16:00 CET; dwunastogodzinne okno pilnuje, zeby
        # kurs doszedl takze wtedy, gdy panel byl o tej porze wylaczony.
        misfire_grace_time=12 * 3600,
    )

    if settings.sb_sd_refresh_enabled:
        scheduler.add_job(
            _scheduled_sb_sd_refresh, "cron",
            hour=settings.sb_sd_refresh_hour, minute=0,
            id="sb_sd_refresh", replace_existing=True,
            max_instances=1, coalesce=True,
            # Panel czesto chodzi na laptopie, ktory w nocy jest wylaczony.
            # Domyslna tolerancja APSchedulera to JEDNA SEKUNDA - zadanie
            # o 4:00 przepadaloby bezpowrotnie i dane nigdy by nie doszly.
            # Z dwunastogodzinnym oknem nocny przebieg odpala sie przy
            # pierwszym uruchomieniu panelu tego dnia, a `coalesce` pilnuje,
            # zeby zaleglosci wykonaly sie raz, nie tyle razy, ile ich bylo.
            misfire_grace_time=12 * 3600,
        )
        log.info(
            "SB/SD: pobranie raz na dobe o %s:00 (okno %s dni). Przy wylaczonym "
            "komputerze nocny przebieg nadrobi sie po starcie panelu, jesli od "
            "planowanej godziny nie minelo wiecej niz 12 godzin.",
            settings.sb_sd_refresh_hour, settings.sb_sd_refresh_days,
        )

    scheduler.start()
    log.info(
        "Scheduler wystartowal: reguly co 30 min (tworza wylacznie sugestie), "
        "odswiezanie danych %s",
        f"co {settings.auto_refresh_hours}h" if settings.auto_refresh_enabled else "wylaczone",
    )

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="AdsPanel",
    version="0.1.0",
    description="Panel kampanii Amazon Ads",
    lifespan=lifespan,
    docs_url="/docs" if settings.api_docs else None,
    redoc_url="/redoc" if settings.api_docs else None,
    openapi_url="/openapi.json" if settings.api_docs else None,
)

@app.middleware("http")
async def guard(request: Request, call_next):
    """Zamyka cale /api poza logowaniem i health.

    Osloniecie middlewarem, a nie zaleznoscia przy kazdej trasie, jest tu
    swiadome: nowy endpoint jest chroniony domyslnie. Przy trzydziestu trasach
    latwo zapomniec dopisac Depends, a zapomniana trasa zapisu to otwarte konto.
    """
    path = request.url.path
    if (
        WYMAGA_LICENCJI
        and request.method != "OPTIONS"
        and path.startswith("/api")
        and path != "/api/health"
    ):
        stan_licencji = licencja.stan()
        if not stan_licencji.ok:
            # 403, nie 401: frontend po 403 pokazuje ekran licencji zamiast
            # logowania, ktore i tak nic by nie dalo.
            return JSONResponse(
                {"detail": stan_licencji.powod, "licencja": stan_licencji.jako_slownik()},
                status_code=403,
            )

    if (
        request.method == "OPTIONS"          # preflight CORS musi przejsc
        or not path.startswith("/api")
        or auth.is_public(path)
        or settings.auth_disabled
        or auth.is_authenticated(request)
    ):
        return await call_next(request)

    return JSONResponse({"detail": "Wymagane zalogowanie"}, status_code=401)


# Ustawienia -> Konto Amazon. Chronione tym samym guardem co reszta /api.
app.include_router(konto_amazon.router)


# CORS musi byc zarejestrowany PO guard: Starlette wstawia kazde kolejne
# middleware na poczatek stosu, wiec ostatnio dodane biegnie jako pierwsze
# (najbardziej zewnetrzne). Gdy CORS byl dodany przed guard, guard zdazyl
# zwrocic 401 zanim CORSMiddleware zdolal dolozyc naglowki - przegladarka
# widziala odpowiedz bez Access-Control-Allow-Origin i zglaszala blad CORS
# zamiast pokazac uzytkownikowi "wymagane zalogowanie".
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,  # niezbedne, zeby przegladarka wysylala ciasteczko sesji
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Wspolne zaleznosci
# --------------------------------------------------------------------------- #


class DateRange:
    def __init__(
        self,
        start: dt.date | None = Query(None, description="YYYY-MM-DD; domyslnie 30 dni wstecz"),
        end: dt.date | None = Query(None, description="YYYY-MM-DD; domyslnie wczoraj"),
        days: int = Query(30, ge=1, le=95),
    ) -> None:
        if start and end:
            self.start, self.end = start, end
        else:
            self.start, self.end = queries.default_range(days)
        if self.start > self.end:
            raise HTTPException(400, "start nie moze byc pozniejszy niz end")


ProfileParam = Query(None, description="profileId; brak = wszystkie rynki razem")


# --------------------------------------------------------------------------- #
# Health / profile / sync
# --------------------------------------------------------------------------- #


def _opis_bazy() -> str:
    """Baza w postaci nadajacej sie do pokazania - BEZ hasla.

    `/api/health` jest celowo publiczny: odpytuje go monitoring i Docker,
    zanim ktokolwiek sie zaloguje. Wczesniej oddawal `DATABASE_URL` uciety po
    `///`, co przy SQLite dawalo sciezke do pliku, ale przy Postgresie **cale
    haslo do bazy** - adres postgresowy nie ma potrojnego ukosnika, wiec nie
    bylo czego uciac. Na serwerze API slucha na 0.0.0.0, czyli haslo dostawal
    kazdy w sieci, jednym `curl` i bez logowania. Sprawdzone na zywym wdrozeniu.

    Teraz skladamy opis z czesci nieszkodliwych: silnik, host, nazwa bazy.
    """
    url = settings.resolved_database_url()
    if url.startswith("sqlite"):
        # Przy SQLite interesuje nas plik - nie ma tu zadnych poswiadczen.
        return url.split("///")[-1]

    rozbite = urlsplit(url)
    host = rozbite.hostname or "?"
    port = f":{rozbite.port}" if rozbite.port else ""
    baza = (rozbite.path or "/").lstrip("/") or "?"
    return f"{rozbite.scheme}://{host}{port}/{baza}"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "wersja": WERSJA,
        "region": settings.ads_region,
        "credentials_configured": settings.is_configured,
        "database": _opis_bazy(),
        "licencja": _stan_licencji().jako_slownik(),
    }


@app.get("/api/profiles")
def get_profiles(session: Session = Depends(get_session)):
    return queries.profiles(session)


@app.post("/api/sync/profiles")
def post_sync_profiles():
    return {"profiles": sync.sync_profiles()}


@app.post("/api/sync/entities/{profile_id}")
def post_sync_entities(profile_id: int):
    return sync.sync_entities(profile_id)


@app.post("/api/sync/reports/{profile_id}")
def post_sync_reports(
    profile_id: int,
    start: dt.date | None = None,
    end: dt.date | None = None,
    kinds: str | None = Query(None, description="np. campaign,search_term"),
):
    kind_list = [k.strip() for k in kinds.split(",")] if kinds else None
    return sync.sync_reports(profile_id, start, end, kind_list)


@app.post("/api/sync/all")
def post_sync_all(
    background: BackgroundTasks,
    lookback_days: int = Query(14, ge=1, le=95),
    with_reports: bool = True,
    wait: bool = Query(False, description="true = czekaj na wynik (moze trwac minuty)"),
):
    # Przebieg obejmuje takze sprzedaz calkowita i podzial FBA (patrz
    # `sync.sync_all`), wiec trwa dluzej niz same reklamy - stad tym bardziej
    # ma sens jeden naraz.
    # Blokade bierzemy tu, nie w zadaniu w tle - inaczej dwa szybkie
    # klikniecia oba dostalyby "started", a dopiero w tle jedno by odpadlo.
    # Kliknietemu drugi raz przyciskowi nalezy sie od razu "juz trwa".
    def _pod_blokada() -> dict:
        try:
            return sync.sync_all(lookback_days=lookback_days, with_reports=with_reports)
        finally:
            sync_lock.zwolnij()

    try:
        sync_lock.zajmij("sync z panelu")
    except sync_lock.Zajete as exc:
        raise HTTPException(409, str(exc))

    if wait:
        return _pod_blokada()
    background.add_task(_pod_blokada)
    return {"status": "started", "lookback_days": lookback_days}


@app.get("/api/sync/status")
def get_sync_status():
    """Czy jakas synchronizacja wlasnie trwa - do wyszarzenia przycisku."""
    return {"running": sync_lock.co_trwa() is not None, "what": sync_lock.co_trwa()}


@app.get("/api/sync/runs")
def get_sync_runs(limit: int = 50, session: Session = Depends(get_session)):
    rows = session.execute(
        select(SyncRun).order_by(desc(SyncRun.started_at)).limit(limit)
    ).scalars().all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "profile_id": r.profile_id,
            "status": r.status,
            "started_at": iso_utc(r.started_at),
            "finished_at": iso_utc(r.finished_at),
            "rows": r.rows,
            "detail": r.detail,
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Analityka
# --------------------------------------------------------------------------- #


@app.get("/api/overview")
def get_overview(
    profile_id: int | None = ProfileParam,
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return {
        "kpis": queries.kpis(session, profile_id, rng.start, rng.end),
        "timeseries": queries.timeseries(session, profile_id, rng.start, rng.end),
        "placements": queries.placements(session, profile_id, rng.start, rng.end),
        "fba": queries.fba_share(session, profile_id, rng.start, rng.end),
    }


@app.get("/api/markets")
def get_markets(rng: DateRange = Depends(), session: Session = Depends(get_session)):
    return queries.markets_overview(session, rng.start, rng.end)


@app.get("/api/portfolios")
def get_portfolios(
    profile_id: int | None = ProfileParam,
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Wyniki per portfolio - odwzorowanie widoku z panelu Amazona."""
    return queries.portfolios(session, profile_id, rng.start, rng.end)


@app.get("/api/campaigns")
def get_campaigns(
    profile_id: int | None = ProfileParam,
    state: str | None = None,
    search: str | None = None,
    portfolio_id: int | None = Query(None, description="0 = kampanie bez portfolio"),
    include_archived: bool = Query(False, description="domyslnie archiwum jest ukryte"),
    limit: int = Query(500, ge=1, le=5000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.campaigns(
        session, profile_id, rng.start, rng.end, state, search, limit,
        portfolio_id, include_archived,
    )


@app.get("/api/campaigns/{campaign_id}")
def get_campaign_detail(
    campaign_id: str,
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Naglowek widoku kampanii - konfiguracja i metryki za okres."""
    row = queries.campaign_detail(session, campaign_id, rng.start, rng.end)
    if row is None:
        raise HTTPException(404, "Kampania nie istnieje w bazie")
    return row


@app.get("/api/ad-groups")
def get_ad_groups(
    profile_id: int | None = ProfileParam,
    campaign_id: str | None = None,
    limit: int = Query(2000, ge=1, le=10000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.ad_groups(session, profile_id, rng.start, rng.end, campaign_id, limit)


@app.get("/api/targets")
def get_targets(
    profile_id: int | None = ProfileParam,
    campaign_id: str | None = None,
    kind: Literal["KEYWORD", "TARGET"] | None = None,
    min_clicks: int = 0,
    limit: int = Query(1000, ge=1, le=10000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.targets(
        session, profile_id, rng.start, rng.end, campaign_id, kind, min_clicks, limit
    )


@app.get("/api/search-terms")
def get_search_terms(
    profile_id: int | None = ProfileParam,
    campaign_id: str | None = None,
    min_clicks: int = 0,
    min_orders: int | None = None,
    max_orders: int | None = None,
    search: str | None = None,
    limit: int = Query(1000, ge=1, le=10000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.search_terms(
        session, profile_id, rng.start, rng.end, campaign_id,
        min_clicks, min_orders, max_orders, search, limit,
    )


@app.get("/api/placements")
def get_placements(
    profile_id: int | None = ProfileParam,
    campaign_id: str | None = None,
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.placements(session, profile_id, rng.start, rng.end, campaign_id)


@app.get("/api/placements/by-campaign")
def get_placements_by_campaign(
    profile_id: int | None = ProfileParam,
    campaign_id: str | None = None,
    limit: int = Query(2000, ge=1, le=10000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.placements_by_campaign(
        session, profile_id, rng.start, rng.end, campaign_id, limit
    )


@app.get("/api/product-ads")
def get_product_ads(
    profile_id: int | None = ProfileParam,
    campaign_id: str | None = None,
    limit: int = Query(5000, ge=1, le=20000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Produkty per grupa reklam - z ad_id pozwalajacym wstrzymac konkretna reklame."""
    return queries.product_ads(session, profile_id, rng.start, rng.end, campaign_id, limit)


class ProductStateRequest(BaseModel):
    profile_id: int
    ad_ids: list[str]
    state: Literal["ENABLED", "PAUSED"]


@app.post("/api/actions/product-ads")
def post_product_ads(req: ProductStateRequest, session: Session = Depends(get_session)):
    """Liczy plan wstrzymania/wznowienia reklam produktowych. Nie wysyla nic."""
    result = actions.plan_product_ads(session, req.profile_id, req.ad_ids, req.state)
    session.commit()
    return result


@app.get("/api/negatives/keywords")
def get_negative_keywords(
    profile_id: int | None = ProfileParam,
    search: str | None = None,
    campaign_id: str | None = None,
    limit: int = Query(5000, ge=1, le=20000),
    session: Session = Depends(get_session),
):
    return queries.negative_keywords(session, profile_id, search, limit, campaign_id)


@app.get("/api/negatives/targets")
def get_negative_targets(
    profile_id: int | None = ProfileParam,
    search: str | None = None,
    campaign_id: str | None = None,
    limit: int = Query(5000, ge=1, le=20000),
    session: Session = Depends(get_session),
):
    return queries.negative_targets(session, profile_id, search, limit, campaign_id)


@app.get("/api/products")
def get_products(
    profile_id: int | None = ProfileParam,
    limit: int = Query(500, ge=1, le=5000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.products(session, profile_id, rng.start, rng.end, limit)


@app.get("/api/suggestions")
def get_suggestions(
    profile_id: int | None = ProfileParam,
    only_actionable: bool = Query(True, description="pomijaj sugestie czysto informacyjne"),
    portfolio_id: int | None = Query(None, description="tylko to portfolio; 0 = bez portfolio"),
    sensitivity: float = Query(
        1.0, ge=0.3, le=1.0,
        description="prog probki: 1.0 = wyliczony z konwersji konta, mniej = slabsze sygnaly",
    ),
    limit: int = Query(500, ge=1, le=2000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Wszystkie sugestie z konta w jednej liscie.

    Kazda dostaje znacznik `critical` - czy stawka przekracza prog, powyzej
    ktorego regula uruchomiona automatycznie w ogole tworzy plan. Dzieki temu
    widac, co jest warte uwagi teraz, a co moze poczekac.
    """
    rows = queries.all_suggestions(
        session, profile_id, rng.start, rng.end, only_actionable, limit,
        portfolio_id, sensitivity,
    )
    prog = settings.auto_queue_min_cost
    for r in rows:
        r["critical"] = (r.get("cost") or 0) >= prog
    return {"threshold": prog, "items": rows}


class DismissItem(BaseModel):
    scope: str
    entity_id: str
    code: str
    title: str | None = None
    entity_name: str | None = None


class DismissRequest(BaseModel):
    profile_id: int
    items: list[DismissItem]
    # Liczba dni; brak = na stale
    days: int | None = Field(None, ge=1, le=3650)


@app.post("/api/suggestions/dismiss")
def post_dismiss_suggestions(req: DismissRequest, session: Session = Depends(get_session)):
    """Odrzuca sugestie - na stale albo na wskazana liczbe dni.

    To decyzja uzytkownika, nie zmiana w Amazonie, wiec nie przechodzi przez
    kolejke zatwierdzen. Ponowne odrzucenie tej samej sugestii nadpisuje
    termin (np. z 30 dni na stale).
    """
    from .models import DismissedSuggestion
    from .timeutil import utcnow

    until = utcnow() + dt.timedelta(days=req.days) if req.days else None
    zapisane = 0
    for it in req.items:
        obj = session.execute(
            select(DismissedSuggestion).where(
                DismissedSuggestion.profile_id == req.profile_id,
                DismissedSuggestion.scope == it.scope,
                DismissedSuggestion.entity_id == it.entity_id,
                DismissedSuggestion.code == it.code,
            )
        ).scalar_one_or_none()
        if obj is None:
            obj = DismissedSuggestion(
                profile_id=req.profile_id, scope=it.scope,
                entity_id=it.entity_id, code=it.code,
            )
            session.add(obj)
        obj.title = it.title
        obj.entity_name = it.entity_name
        obj.dismissed_at = utcnow()
        obj.until = until
        zapisane += 1
    session.commit()
    return {"dismissed": zapisane, "until": until}


@app.get("/api/suggestions/dismissed")
def get_dismissed_suggestions(
    profile_id: int | None = ProfileParam, session: Session = Depends(get_session)
):
    """Lista odrzuconych, wciaz aktywnych - do przejrzenia i przywrocenia."""
    from .models import DismissedSuggestion
    from .timeutil import utcnow

    warunki = [
        (DismissedSuggestion.until.is_(None)) | (DismissedSuggestion.until > utcnow())
    ]
    if profile_id:
        warunki.append(DismissedSuggestion.profile_id == profile_id)
    rows = session.execute(
        select(DismissedSuggestion).where(*warunki)
        .order_by(DismissedSuggestion.dismissed_at.desc())
    ).scalars().all()
    return [
        {
            "id": r.id, "profile_id": r.profile_id, "scope": r.scope,
            "entity_id": r.entity_id, "code": r.code, "title": r.title,
            "entity_name": r.entity_name, "dismissed_at": r.dismissed_at,
            "until": r.until,
        }
        for r in rows
    ]


@app.delete("/api/suggestions/dismissed/{dismiss_id}")
def delete_dismissed_suggestion(dismiss_id: int, session: Session = Depends(get_session)):
    """Przywraca sugestie - od nastepnego odswiezenia znow jest na liscie."""
    from .models import DismissedSuggestion

    obj = session.get(DismissedSuggestion, dismiss_id)
    if obj is None:
        raise HTTPException(404, "Nie ma takiego odrzucenia")
    session.delete(obj)
    session.commit()
    return {"restored": dismiss_id}


# --------------------------------------------------------------------------- #
# Sprzedaz calkowita - mianownik do TACOS
# --------------------------------------------------------------------------- #


class SalesPreviewRequest(BaseModel):
    """Tresc CSV przekazujemy jako tekst, nie jako upload wieloczesciowy.

    Dzieki temu backend nie potrzebuje python-multipart, a front i tak czyta
    plik lokalnie, zeby pokazac podglad przed wyslaniem.
    """
    content: str


class SalesImportRequest(BaseModel):
    profile_id: int
    content: str
    mapping: dict[str, str | None]
    delimiter: str = ","


class ManualSalesEntry(BaseModel):
    date: dt.date
    sales: float
    units: int = 0
    orders: int = 0


class ManualSalesRequest(BaseModel):
    profile_id: int
    entries: list[ManualSalesEntry]


@app.post("/api/total-sales/preview")
def post_sales_preview(req: SalesPreviewRequest):
    """Rozpoznaje separator i kolumny. Nic nie zapisuje."""
    if not req.content.strip():
        raise HTTPException(422, "Plik jest pusty.")
    try:
        return business_report.sniff(req.content)
    except Exception as exc:  # noqa: BLE001 - komunikat ma trafic do uzytkownika
        raise HTTPException(422, f"Nie udało się odczytać pliku: {exc}") from exc


@app.post("/api/total-sales/import")
def post_sales_import(req: SalesImportRequest, session: Session = Depends(get_session)):
    """Wczytuje sprzedaz calkowita z CSV.

    W odroznieniu od zmian w kampaniach ten import nie wymaga zatwierdzania:
    niczego nie wysyla do Amazona, tylko dokłada dane po naszej stronie.
    """
    try:
        parsed = business_report.parse(
            req.content, req.profile_id, req.mapping, req.delimiter
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if not parsed["rows"]:
        raise HTTPException(
            422,
            "Nie znaleziono ani jednego wiersza z datą i kwotą. "
            "Sprawdź przypisanie kolumn.",
        )

    wynik = total_sales.store(session, parsed["rows"])
    session.commit()
    return {**wynik, **{k: parsed[k] for k in
                        ("skipped_no_date", "skipped_no_amount", "total_sales", "date_format")}}


@app.post("/api/total-sales/manual")
def post_sales_manual(req: ManualSalesRequest, session: Session = Depends(get_session)):
    """Reczne wpisanie sprzedazy - gdy nie chce sie eksportowac raportu."""
    if not req.entries:
        raise HTTPException(422, "Nie podano żadnych kwot.")

    rows = [
        {
            "date": e.date, "profile_id": req.profile_id, "asin": None, "sku": None,
            "sales": round(float(e.sales), 2), "units": e.units, "orders": e.orders,
            "sessions": None, "source": "MANUAL",
        }
        for e in req.entries
    ]
    wynik = total_sales.store(session, rows)
    session.commit()
    return wynik


@app.get("/api/total-sales/coverage")
def get_sales_coverage(
    profile_id: int | None = ProfileParam, session: Session = Depends(get_session)
):
    """Za jakie okresy mamy sprzedaz calkowita i ile jej jest."""
    return total_sales.coverage(session, profile_id)


@app.delete("/api/total-sales")
def delete_sales(
    profile_id: int,
    start: dt.date,
    end: dt.date,
    session: Session = Depends(get_session),
):
    """Kasuje zaimportowana sprzedaz z zakresu - do poprawienia zlego importu."""
    from .models import TotalSalesDaily

    wynik = session.execute(
        sa_delete(TotalSalesDaily).where(
            TotalSalesDaily.profile_id == profile_id,
            TotalSalesDaily.date >= start,
            TotalSalesDaily.date <= end,
        )
    )
    session.commit()
    return {"deleted": wynik.rowcount or 0}


class SuggestionAction(BaseModel):
    type: Literal[
        "SET_BID", "SET_BUDGET", "ADD_NEGATIVE", "ADD_NEGATIVE_ASIN",
        "SET_PLACEMENT", "PAUSE_TARGET", "HARVEST", "PAUSE_PRODUCT",
    ]
    target_id: str | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None
    keyword_text: str | None = None
    match_type: str | None = None
    placement: str | None = None
    kind: str | None = None
    asin: str | None = None
    ad_id: str | None = None
    value: float | None = None
    # Uzasadnienie z panelu sugestii. Wedruje az do kolejki, zeby przy
    # zatwierdzaniu bylo widac, dlaczego ta zmiana w ogole sie tam znalazla -
    # sam wiersz "stawka 0,23 -> 0,26" tego nie tlumaczy.
    reason: str | None = None


class SuggestionApplyRequest(BaseModel):
    profile_id: int
    actions: list[SuggestionAction]


@app.post("/api/suggestions/queue")
def post_queue_suggestions(
    req: SuggestionApplyRequest,
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Zamienia wybrane sugestie na plany czekajace na zatwierdzenie.

    Nic nie idzie do Amazona - powstaja wpisy w kolejce, tak samo jak przy
    akcjach recznych i regulach. Kazdy typ akcji trafia do osobnego planu,
    bo Ads API obsluguje je innymi zasobami.
    """
    acts = list(req.actions)
    plans: list[dict[str, Any]] = []

    bids = [
        {"id": a.target_id, "kind": a.kind or "KEYWORD", "bid": a.value,
         "reason": a.reason}
        for a in acts
        if a.type == "SET_BID" and a.target_id and a.value
    ]
    if bids:
        plans.append(actions.plan_bids(session, req.profile_id, bids, source="SUGGESTION"))

    budgets = [
        {"campaign_id": a.campaign_id, "budget": a.value, "reason": a.reason}
        for a in acts
        if a.type == "SET_BUDGET" and a.campaign_id and a.value
    ]
    if budgets:
        plans.append(actions.plan_budgets(session, req.profile_id, budgets, source="SUGGESTION"))

    negatives = [
        {
            "campaignId": a.campaign_id,
            "adGroupId": a.ad_group_id,
            "keywordText": a.keyword_text,
            "matchType": a.match_type or "NEGATIVE_EXACT",
            "reason": a.reason,
        }
        for a in acts
        if a.type == "ADD_NEGATIVE" and a.campaign_id and a.ad_group_id and a.keyword_text
    ]
    if negatives:
        plans.append(
            actions.plan_negatives(session, req.profile_id, negatives, source="SUGGESTION")
        )

    neg_asins = [
        {"campaignId": a.campaign_id, "adGroupId": a.ad_group_id, "asin": a.asin}
        for a in acts
        if a.type == "ADD_NEGATIVE_ASIN" and a.campaign_id and a.ad_group_id and a.asin
    ]
    if neg_asins:
        plans.append(
            actions.plan_negative_targets(
                session, req.profile_id, neg_asins, source="SUGGESTION"
            )
        )

    harvest = [
        {
            "campaignId": a.campaign_id,
            "adGroupId": a.ad_group_id,
            "keywordText": a.keyword_text,
            "matchType": a.match_type or "EXACT",
            # Stawka przychodzi z sugestii i moze byc recznie poprawiona na
            # ekranie; bez niej keyword dostaje stawke domyslna grupy.
            **({"bid": a.value} if a.value is not None else {}),
        }
        for a in acts
        if a.type == "HARVEST" and a.campaign_id and a.ad_group_id and a.keyword_text
    ]
    if harvest:
        plans.append(
            actions.plan_harvest(session, req.profile_id, harvest, source="SUGGESTION")
        )

    product_pauses = [a.ad_id for a in acts if a.type == "PAUSE_PRODUCT" and a.ad_id]
    if product_pauses:
        plans.append(
            actions.plan_product_ads(
                session, req.profile_id, product_pauses, "PAUSED", source="SUGGESTION"
            )
        )

    # Keyword i target produktowy to w Ads API dwa rozne zasoby, z osobnymi
    # identyfikatorami. Dotad wszystko szlo jako "TARGET", wiec wstrzymanie
    # keyworda szukalo jego id wsrod targetow i nie znajdowalo nic.
    pauzy = [a for a in acts if a.type == "PAUSE_TARGET" and a.target_id]
    po_typie: dict[str, list[str]] = {}
    for a in pauzy:
        typ = "KEYWORD" if (a.kind or "") == "KEYWORD" else "TARGET"
        po_typie.setdefault(typ, []).append(a.target_id)
    for typ, ids in po_typie.items():
        plans.append(
            actions.plan_state(
                session, req.profile_id, typ,
                list(dict.fromkeys(ids)), "PAUSED", source="SUGGESTION",
            )
        )

    # Modyfikatory placementow: Amazon nadpisuje caly zestaw, wiec dla kazdej
    # dotknietej kampanii odtwarzamy komplet - zmienione i niezmienione razem
    placement_acts = [a for a in acts if a.type == "SET_PLACEMENT" and a.campaign_id]
    if placement_acts:
        current = {
            (r["campaign_id"], r["placement"]): r
            for r in queries.placements_by_campaign(
                session, req.profile_id, rng.start, rng.end, limit=5000
            )
        }
        by_campaign: dict[str, dict[str, float]] = {}
        for a in placement_acts:
            by_campaign.setdefault(a.campaign_id, {})[a.placement or ""] = a.value or 0

        changes = []
        for cid, wanted in by_campaign.items():
            slots = []
            for placement in (
                "PLACEMENT_TOP", "PLACEMENT_REST_OF_SEARCH", "PLACEMENT_PRODUCT_PAGE"
            ):
                row = current.get((cid, placement))
                old = float((row or {}).get("modifier") or 0)
                slots.append({
                    "placement": placement,
                    "old": old,
                    "new": float(wanted.get(placement, old)),
                })
            campaign = session.get(Campaign, cid)
            changes.append({
                "campaign_id": cid,
                "strategy": campaign.bidding_strategy if campaign else None,
                "placements": slots,
            })
        plans.append(
            actions.plan_placements(session, req.profile_id, changes, source="SUGGESTION")
        )

    # Kazdy plan zapamietuje, ktore sugestie go utworzyly. Dzieki temu lista
    # sugestii pomija to, co juz czeka w kolejce - inaczej ta sama zmiana
    # wisialaby w dwoch miejscach i latwo byloby wyslac ja drugi raz.
    klucze = [actions.action_key(a.model_dump()) for a in acts]
    if klucze and plans:
        from .models import PendingPlan

        # Bez flush swiezo dodany plan nie jest jeszcze w mapie tozsamosci
        # i session.get zwraca None - klucze przepadaly po cichu.
        session.flush()
        for opis in plans:
            # Plan moze nie powstac - np. gdy wszystkie pozycje odpadly i nie ma
            # czego zatwierdzac. Wtedy `plan_id` jest puste i pytanie o nie
            # skonczyloby sie bledem, a sugestia i tak nie ma byc ukrywana:
            # skoro nic nie trafilo do kolejki, ma zostac na liscie.
            if not opis.get("plan_id"):
                continue
            obj = session.get(PendingPlan, opis["plan_id"])
            if obj is None:
                continue
            obj.payload = {**(obj.payload or {}), "suggestion_keys": klucze}

    session.commit()
    # Liczymy tylko plany, ktore naprawde trafily do kolejki. Wpis bez
    # `plan_id` niesie wylacznie powody pominiecia - pokazujemy go, ale
    # zliczanie go jako "zakolejkowany" byloby nieprawda.
    realne = [p for p in plans if p.get("plan_id")]
    return {
        "queued": len(realne),
        "items": sum(p.get("item_count", 0) for p in realne),
        "plans": plans,
    }


@app.get("/api/opportunities/negatives")
def get_negative_candidates(
    profile_id: int | None = ProfileParam,
    min_clicks: int = Query(10, ge=1),
    max_orders: int = Query(0, ge=0),
    min_cost: float = Query(0.0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.negative_candidates(
        session, profile_id, rng.start, rng.end, min_clicks, max_orders, min_cost, limit
    )


@app.get("/api/home/status")
def get_home_status(
    profile_id: int | None = ProfileParam,
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Swiezosc danych i to, co czeka na decyzje - naglowek strony glownej."""
    return {
        "freshness": queries.data_freshness(session, profile_id),
        "attention": queries.attention(session, profile_id, rng.start, rng.end),
    }


@app.get("/api/monthly")
def get_monthly(
    profile_id: int | None = ProfileParam,
    months: int = Query(6, ge=2, le=24),
    session: Session = Depends(get_session),
):
    """Wyniki miesiac po miesiacu - do porownania okresow na stronie glownej."""
    return queries.monthly(session, profile_id, months)


@app.get("/api/portfolios/timeseries")
def get_portfolio_timeseries(
    profile_id: int | None = ProfileParam,
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Dzienny przebieg kazdego portfolio - do wykresow w tabeli portfolii."""
    return queries.portfolio_timeseries(session, profile_id, rng.start, rng.end)


@app.get("/api/opportunities/overlap")
def get_internal_competition(
    profile_id: int | None = ProfileParam,
    min_campaigns: int = Query(2, ge=2, le=20),
    min_clicks: int = Query(10, ge=1),
    limit: int = Query(300, ge=1, le=2000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Frazy, ktore zbiera kilka wlasnych kampanii naraz."""
    return queries.internal_competition(
        session, profile_id, rng.start, rng.end, min_campaigns, min_clicks, limit
    )


@app.get("/api/opportunities/sku-overlap")
def get_sku_overlap(
    profile_id: int | None = ProfileParam,
    min_campaigns: int = Query(2, ge=2, le=20),
    min_clicks: int = Query(10, ge=1),
    limit: int = Query(300, ge=1, le=2000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Ten sam produkt reklamowany w kilku kampaniach naraz."""
    return queries.sku_overlap(
        session, profile_id, rng.start, rng.end, min_campaigns, min_clicks, limit
    )


@app.get("/api/catalog/search")
def get_catalog_search(
    profile_id: int = Query(..., description="rynek, z ktorego bierzemy asortyment"),
    q: str | None = Query(None, description="fragment nazwy, SKU albo ASIN"),
    limit: int = Query(40, ge=1, le=200),
    session: Session = Depends(get_session),
):
    """Produkty tego rynku - do wyboru w formularzu nowej kampanii."""
    return queries.catalog_search(session, profile_id, q, limit)


@app.get("/api/catalog/brands")
def get_brands(
    profile_id: int = Query(..., description="rynek, ktorego dotyczy slownik marek"),
    q: str | None = Query(None, description="fragment nazwy marki"),
    limit: int = Query(60, ge=1, le=200),
    session: Session = Depends(get_session),
):
    """Marki do wykluczenia - ze slownika zbudowanego z kategorii konta."""
    from .etl import labels as labels_etl
    from .models import Profile

    mp = session.execute(
        select(Profile.marketplace_id).where(Profile.profile_id == profile_id)
    ).scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Profil nie ma przypisanego marketplace")
    return labels_etl.marki(session, mp, q, limit)


@app.post("/api/catalog/brands/refresh")
def post_brands_refresh(
    profile_id: int = Query(..., description="rynek, dla ktorego pobrac marki"),
    session: Session = Depends(get_session),
):
    """Pobiera liste marek z uszczegolowien kategorii, ktore konto targetuje.

    To wylacznie ODCZYT z Amazona; trwa kilkanascie sekund (jedno zapytanie
    na kategorie), wiec wolamy je na zadanie, a nie przy kazdym otwarciu
    wyszukiwarki.
    """
    from .etl import labels as labels_etl
    from .models import Profile

    mp = session.execute(
        select(Profile.marketplace_id).where(Profile.profile_id == profile_id)
    ).scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Profil nie ma przypisanego marketplace")
    return labels_etl.dociagnij_marki(profile_id, mp)


@app.get("/api/anomalies/contributors")
def get_anomaly_contributors(
    campaign_id: str = Query(..., description="kampania, ktorej dotyczy alert"),
    session: Session = Depends(get_session),
):
    """Frazy, ktore najbardziej zmienily zachowanie tej kampanii."""
    return anomalies.contributors(session, campaign_id)


@app.get("/api/effectiveness")
def get_effectiveness(
    profile_id: int | None = ProfileParam,
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
):
    """Co dzialo sie przed i po kazdej wprowadzonej zmianie.

    Bez zakresu dat: okno wyznacza data samej zmiany, nie filtr u gory.
    """
    return effectiveness.analyze(session, profile_id, limit=limit)


@app.get("/api/anomalies")
def get_anomalies(
    profile_id: int | None = ProfileParam,
    limit: int = Query(60, ge=1, le=300),
    session: Session = Depends(get_session),
):
    """Kampanie, w ktorych ostatnie dni odstaja od ich wlasnej normy.

    Bez parametru zakresu celowo: okno jest ustalone przez sama metode
    (ostatnie dni wobec trzech tygodni normy), a nie przez filtr u gory.
    """
    return anomalies.detect(session, profile_id, limit=limit)


@app.get("/api/opportunities/ngram-breakdown")
def get_ngram_breakdown(
    ngram: str = Query(..., min_length=1, description="fragment frazy do rozbicia"),
    profile_id: int | None = ProfileParam,
    limit: int = Query(40, ge=1, le=200),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Gdzie wystepuje ten n-gram - w rozbiciu na grupy reklam."""
    return queries.ngram_breakdown(session, profile_id, ngram, rng.start, rng.end, limit)


@app.get("/api/opportunities/budget-capped")
def get_budget_capped(
    profile_id: int | None = ProfileParam,
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Kampanie, ktore regularnie wyczerpuja dzienny budzet."""
    return queries.budget_capped(session, profile_id, rng.start, rng.end)


@app.get("/api/opportunities/harvest")
def get_harvest_candidates(
    profile_id: int | None = ProfileParam,
    min_orders: int = Query(2, ge=1),
    max_acos: float = Query(35.0, gt=0),
    limit: int = Query(500, ge=1, le=5000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    return queries.harvest_candidates(
        session, profile_id, rng.start, rng.end, min_orders, max_acos, limit
    )


@app.get("/api/ngrams")
def get_ngrams(
    profile_id: int | None = ProfileParam,
    max_n: int = Query(3, ge=1, le=4, description="najdluzszy fragment w slowach"),
    min_clicks: int | None = Query(
        None, ge=1,
        description="domyslnie wyliczany z konwersji konta - tyle klikniec, "
                    "zeby brak sprzedazy cos znaczyl",
    ),
    show_all: bool = Query(False, description="pomin progi i pokaz wszystko"),
    limit: int = Query(200, ge=1, le=2000),
    rng: DateRange = Depends(),
    session: Session = Depends(get_session),
):
    """Fragmenty fraz przepalajace budzet - jedna lista dla 1, 2 i 3 slow."""
    return queries.ngram_analysis(
        session, profile_id, rng.start, rng.end,
        max_n=max_n, min_clicks=min_clicks, limit=limit, show_all=show_all,
    )


# --------------------------------------------------------------------------- #
# Akcje zapisu
# --------------------------------------------------------------------------- #


class BidChange(BaseModel):
    id: str
    kind: Literal["KEYWORD", "TARGET"] = "KEYWORD"
    bid: float = Field(gt=0)


class BidRequest(BaseModel):
    profile_id: int
    changes: list[BidChange]


class StateRequest(BaseModel):
    profile_id: int
    entity_type: Literal["KEYWORD", "TARGET", "CAMPAIGN", "AD_GROUP"]
    ids: list[str]
    state: Literal["ENABLED", "PAUSED", "ARCHIVED"]


class AddProductsRequest(BaseModel):
    profile_id: int
    ad_group_id: str
    skus: list[str] = []
    asins: list[str] = []


class RemoveNegativesRequest(BaseModel):
    profile_id: int
    keyword_ids: list[str] = []
    target_ids: list[str] = []


class BudgetChange(BaseModel):
    campaign_id: str
    budget: float = Field(gt=0)


class BudgetRequest(BaseModel):
    profile_id: int
    changes: list[BudgetChange]


class PlacementSlot(BaseModel):
    placement: str
    old: float
    new: float


class PlacementChange(BaseModel):
    campaign_id: str
    strategy: str | None = None
    placements: list[PlacementSlot]


class PlacementRequest(BaseModel):
    profile_id: int
    changes: list[PlacementChange]


class NegativeItem(BaseModel):
    campaignId: str
    adGroupId: str | None = None
    keywordText: str
    matchType: Literal["NEGATIVE_EXACT", "NEGATIVE_PHRASE"] = "NEGATIVE_EXACT"


class NegativeRequest(BaseModel):
    profile_id: int
    items: list[NegativeItem]
    level: Literal["AD_GROUP", "CAMPAIGN"] = "AD_GROUP"


class HarvestItem(BaseModel):
    campaignId: str
    adGroupId: str
    keywordText: str
    matchType: Literal["EXACT", "PHRASE", "BROAD"] = "EXACT"
    bid: float | None = None


class HarvestRequest(BaseModel):
    profile_id: int
    items: list[HarvestItem]


class ApplyRequest(BaseModel):
    """Zatwierdzenie policzonego planu - jedyna droga do zapisu w Amazonie.

    `indexes` (opcjonalne) ogranicza zatwierdzenie do wskazanych pozycji planu;
    pominiete zostaja w kolejce.
    """

    plan_id: str
    indexes: list[int] | None = None


class CreateCampaignRequest(BaseModel):
    profile_id: int
    spec: dict[str, Any]


@app.post("/api/actions/create-campaign")
def post_create_campaign(req: CreateCampaignRequest, session: Session = Depends(get_session)):
    """Liczy plan utworzenia kampanii. Nic nie powstaje bez zatwierdzenia."""
    try:
        result = actions.plan_create_campaign(session, req.profile_id, req.spec)
        session.commit()
        return result
    except actions.SpecError as exc:
        # Blad w opisie kampanii to wina wejscia, nie serwera - 422, zeby front
        # mogl pokazac tresc uzytkownikowi zamiast komunikatu o awarii.
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/actions/bids")
def post_bids(req: BidRequest, session: Session = Depends(get_session)):
    """Liczy plan zmiany stawek. Nie wysyla nic do Amazona."""
    result = actions.plan_bids(session, req.profile_id, [c.model_dump() for c in req.changes])
    session.commit()
    return result


@app.post("/api/actions/state")
def post_state(req: StateRequest, session: Session = Depends(get_session)):
    """Liczy plan zmiany statusu. Nie wysyla nic do Amazona."""
    result = actions.plan_state(session, req.profile_id, req.entity_type, req.ids, req.state)
    session.commit()
    return result


@app.post("/api/actions/add-products")
def post_add_products(req: AddProductsRequest, session: Session = Depends(get_session)):
    """Liczy plan dodania produktow do grupy reklam. Nie wysyla nic do Amazona."""
    result = actions.plan_add_products(
        session, req.profile_id, req.ad_group_id, req.skus, req.asins
    )
    session.commit()
    return result


class NegativeAsinItem(BaseModel):
    campaignId: str
    adGroupId: str
    asin: str


class NegativeBrandItem(BaseModel):
    campaignId: str
    adGroupId: str
    # Amazon identyfikuje marke liczba z uszczegolowien kategorii; nazwa jest
    # tylko po to, zeby kolejka zatwierdzen nie pokazywala golego numeru.
    brandId: str
    brandName: str | None = None


class NegativeAsinRequest(BaseModel):
    profile_id: int
    items: list[NegativeAsinItem] = []
    brands: list[NegativeBrandItem] = []


@app.post("/api/actions/negative-targets")
def post_negative_targets(
    req: NegativeAsinRequest, session: Session = Depends(get_session)
):
    """Liczy plan wykluczenia ASIN-ow i marek. Nie wysyla nic do Amazona.

    Wykluczenia produktowe - tak pojedynczych ASIN-ow, jak calych marek -
    istnieja tylko na poziomie grupy reklam; Amazon nie ma ich odpowiednika
    dla calej kampanii.
    """
    pozycje = [i.model_dump() for i in req.items] + [b.model_dump() for b in req.brands]
    if not pozycje:
        raise HTTPException(400, "Nie podano ani ASIN-ow, ani marek")
    result = actions.plan_negative_targets(session, req.profile_id, pozycje)
    session.commit()
    return result


@app.post("/api/actions/remove-negatives")
def post_remove_negatives(
    req: RemoveNegativesRequest, session: Session = Depends(get_session)
):
    """Liczy plan usuniecia wykluczen. Nie wysyla nic do Amazona."""
    result = actions.plan_remove_negatives(
        session, req.profile_id, req.keyword_ids, req.target_ids
    )
    session.commit()
    return result


@app.post("/api/actions/budgets")
def post_budgets(req: BudgetRequest, session: Session = Depends(get_session)):
    """Liczy plan zmiany budzetow. Nie wysyla nic do Amazona."""
    result = actions.plan_budgets(session, req.profile_id, [c.model_dump() for c in req.changes])
    session.commit()
    return result


@app.post("/api/actions/placements")
def post_placements(req: PlacementRequest, session: Session = Depends(get_session)):
    """Liczy plan modyfikatorow placementow. Nie wysyla nic do Amazona."""
    result = actions.plan_placements(
        session, req.profile_id, [c.model_dump() for c in req.changes]
    )
    session.commit()
    return result


@app.post("/api/actions/negatives")
def post_negatives(req: NegativeRequest, session: Session = Depends(get_session)):
    """Liczy plan dodania negatywow. Nie wysyla nic do Amazona."""
    result = actions.plan_negatives(
        session, req.profile_id, [i.model_dump(exclude_none=True) for i in req.items],
        level=req.level,
    )
    session.commit()
    return result


@app.post("/api/actions/harvest")
def post_harvest(req: HarvestRequest, session: Session = Depends(get_session)):
    """Liczy plan dodania keywordow. Nie wysyla nic do Amazona."""
    result = actions.plan_harvest(
        session, req.profile_id, [i.model_dump(exclude_none=True) for i in req.items]
    )
    session.commit()
    return result


@app.get("/api/plans")
def get_pending_plans(
    profile_id: int | None = ProfileParam,
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
):
    """Kolejka zatwierdzen - plany reczne i sugestie z regul czekajace na decyzje."""
    return actions.pending_plans(session, profile_id, limit)


@app.post("/api/plans/apply")
def post_apply_plan(req: ApplyRequest, session: Session = Depends(get_session)):
    """Zatwierdza plan i dopiero teraz wysyla zmiany do Amazona."""
    try:
        result = actions.apply_plan(session, req.plan_id, req.indexes)
    except actions.PlanError as exc:
        session.commit()  # zapisz ewentualne oznaczenie planu jako EXPIRED
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    return result


@app.post("/api/plans/cancel")
def post_cancel_plan(req: ApplyRequest, session: Session = Depends(get_session)):
    try:
        result = actions.cancel_plan(session, req.plan_id)
    except actions.PlanError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    return result


@app.get("/api/changes")
def get_changes(
    profile_id: int | None = ProfileParam,
    limit: int = Query(200, ge=1, le=2000),
    session: Session = Depends(get_session),
):
    return actions.recent_changes(session, profile_id, limit)


# --------------------------------------------------------------------------- #
# Reguly
# --------------------------------------------------------------------------- #


class RulePayload(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True
    profile_id: int | None = None
    scope: Literal["TARGET", "SEARCH_TERM", "CAMPAIGN", "PLACEMENT", "PRODUCT"]
    lookback_days: int = Field(30, ge=1, le=95)
    filters: dict[str, Any] | None = None
    conditions: dict[str, Any]
    action: dict[str, Any]
    limits: dict[str, Any] | None = None
    schedule_hours: int | None = Field(None, ge=1, le=720)


def _last_run(session: Session, rule_id: int) -> RuleRun | None:
    return session.execute(
        select(RuleRun).where(RuleRun.rule_id == rule_id).order_by(desc(RuleRun.ts)).limit(1)
    ).scalars().first()


@app.get("/api/rules/meta")
def get_rules_meta():
    """Slowniki dla formularza: zakresy, metryki, operatory, dozwolone akcje."""
    return {
        "scopes": rules_engine.SCOPES,
        "metrics": rules_engine.METRICS,
        "operators": list(rules_engine.OPS),
        "scope_actions": rules_engine.SCOPE_ACTIONS,
        "templates": rule_templates.TEMPLATES,
    }


@app.get("/api/rules")
def get_rules(session: Session = Depends(get_session)):
    rules = session.execute(select(Rule).order_by(Rule.id)).scalars().all()
    return [rules_engine.to_dict(r, _last_run(session, r.id)) for r in rules]


@app.post("/api/rules")
def post_rule(payload: RulePayload, session: Session = Depends(get_session)):
    data = payload.model_dump()
    try:
        rules_engine.validate(data)
    except rules_engine.RuleError as exc:
        raise HTTPException(400, str(exc)) from exc

    rule = Rule(**data)
    session.add(rule)
    session.commit()
    return rules_engine.to_dict(rule)


@app.put("/api/rules/{rule_id}")
def put_rule(rule_id: int, payload: RulePayload, session: Session = Depends(get_session)):
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(404, "Reguła nie istnieje")

    data = payload.model_dump()
    try:
        rules_engine.validate(data)
    except rules_engine.RuleError as exc:
        raise HTTPException(400, str(exc)) from exc

    for key, value in data.items():
        setattr(rule, key, value)
    session.commit()
    return rules_engine.to_dict(rule, _last_run(session, rule.id))


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, session: Session = Depends(get_session)):
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(404, "Reguła nie istnieje")
    session.delete(rule)
    session.commit()
    return {"deleted": rule_id}


@app.post("/api/rules/{rule_id}/preview")
def post_rule_preview(
    rule_id: int,
    profile_id: int = Query(..., description="rynek, na którym liczymy podgląd"),
    session: Session = Depends(get_session),
):
    """Pokazuje, co reguła by złapała. Nie tworzy planu ani nic nie wysyła."""
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(404, "Reguła nie istnieje")
    try:
        return rules_engine.preview_rule(session, rule, profile_id)
    except rules_engine.RuleError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/rules/{rule_id}/run")
def post_rule_run(
    rule_id: int,
    profile_id: int | None = ProfileParam,
    session: Session = Depends(get_session),
):
    """Uruchamia regułę. Efektem są plany w kolejce zatwierdzeń — nic nie idzie
    do Amazona, dopóki nie klikniesz „Zatwierdź i wyślij"."""
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(404, "Reguła nie istnieje")

    results = rules_engine.run_rule(session, rule, profile_id)
    session.commit()
    return {"rule_id": rule_id, "name": rule.name, "results": results}


@app.get("/api/rules/{rule_id}/runs")
def get_rule_runs(rule_id: int, limit: int = Query(30, ge=1, le=200),
                  session: Session = Depends(get_session)):
    runs = session.execute(
        select(RuleRun).where(RuleRun.rule_id == rule_id).order_by(desc(RuleRun.ts)).limit(limit)
    ).scalars().all()
    return [
        {
            "id": r.id,
            "ts": iso_utc(r.ts),
            "profile_id": r.profile_id,
            "evaluated": r.evaluated,
            "matched": r.matched,
            "status": r.status,
            "plan_id": r.plan_id,
            "detail": r.detail,
        }
        for r in runs
    ]


# --------------------------------------------------------------------------- #
# Logowanie
# --------------------------------------------------------------------------- #


class LoginRequest(BaseModel):
    password: str


@app.post("/api/auth/login")
def post_login(req: LoginRequest, response: Response):
    try:
        ok = auth.verify_password(req.password)
    except auth.AuthError as exc:
        raise HTTPException(500, str(exc)) from exc

    if not ok:
        # Bez rozrozniania "zle haslo" od "brak uzytkownika" - jeden uzytkownik,
        # wiec i tak nie ma czego zdradzac
        raise HTTPException(401, "Nieprawidłowe hasło")

    auth.issue_session(response)
    return {"authenticated": True}


@app.post("/api/auth/logout")
def post_logout(response: Response):
    auth.clear_session(response)
    return {"authenticated": False}


@app.get("/api/auth/me")
def get_me(request: Request):
    return {
        "authenticated": settings.auth_disabled or auth.is_authenticated(request),
        "auth_disabled": settings.auth_disabled,
        "configured": settings.auth_ready,
    }

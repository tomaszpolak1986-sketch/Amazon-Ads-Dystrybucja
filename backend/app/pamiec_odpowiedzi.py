"""Pamiec gotowych odpowiedzi zestawien.

Strona glowna, Sugestie i tabele licza to samo po kilka razy: kazdy ekran
odpytuje kilka zestawien naraz, przelaczenie zakladki i powrot liczy je od
nowa, a strona glowna i ekran Sugestii licza ten sam komplet sugestii.
Dane zmieniaja sie tylko przy pobieraniu z Amazona i przy decyzjach
uzytkownika - miedzy nimi kazde z tych przeliczen daje identyczny wynik.

Kiedy wynik przestaje byc aktualny:

* po kazdym zapytaniu zmieniajacym cokolwiek (POST/PUT/PATCH/DELETE) -
  plan, odrzucenie sugestii, ustawienia; tu nie ma wyjatkow,
* po kazdym przebiegu pobierania (zwolnienie `sync_lock`),
* gdy zmienil sie stan kolejki planow, odrzuconych sugestii albo historii
  zmian - to wychwytuje zmiany robione w tle (reguly co 30 min),
* najpozniej po `WAZNOSC` sekundach (12 h).

Te same zapytania liczone rownolegle (dwie karty, ponowienie po bledzie)
czekaja na jeden wynik zamiast liczyc go kilka razy.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
import zlib
from collections import OrderedDict

from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger(__name__)

# Zmiany danych i decyzji kasuja pamiec od razu - limit czasu jest tylko
# zabezpieczeniem na wypadek zmiany, ktorej nic nie zglosilo.
WAZNOSC = 12 * 3600
MAKS_BAJTOW = 100 * 1024 * 1024  # po kompresji

# Zestawienia - wylacznie odczyt, wynik zalezy tylko od adresu i danych.
_SCIEZKI = (
    "/api/overview", "/api/markets", "/api/portfolios", "/api/campaigns",
    "/api/ad-groups", "/api/targets", "/api/search-terms", "/api/placements",
    "/api/product-ads", "/api/products", "/api/negatives/", "/api/suggestions",
    "/api/opportunities/", "/api/home/status", "/api/monthly", "/api/ngrams",
    "/api/anomalies", "/api/effectiveness",
)
_POMIJANE = ("/api/suggestions/dismissed",)

_lock = threading.Lock()
_wpisy: OrderedDict[str, tuple[float, int, str, bytes, dict[str, str]]] = OrderedDict()
_bajty = 0
_pokolenie = 0
_pokolenie_danych = 0
_w_toku: dict[str, asyncio.Future] = {}

# Zapytania, ktore zmieniaja wylacznie DECYZJE (kolejka, odrzucenia, liczenie
# planow), a nie dane ani ustawienia oceny. Po nich gotowe odpowiedzi ekranow
# i tak kasujemy, ale ocenione skladniki sugestii zostaja: ukrycie pozycji
# zakolejkowanych i odrzuconych robi `all_suggestions` przy kazdym wywolaniu.
# Bez tego kazde "do kolejki" liczylo cala liste od nowa - 20-30 s na DE.
_TYLKO_DECYZJE = (
    "/api/suggestions/queue", "/api/suggestions/dismiss",
    "/api/suggestions/dismissed/", "/api/actions/", "/api/plans/cancel",
    "/api/auth/",
)


def uniewaznij(_powod: str = "", dane: bool = True) -> None:
    """Kasuje gotowe odpowiedzi; przy `dane` takze ocenione skladniki."""
    global _bajty, _pokolenie, _pokolenie_danych
    with _lock:
        _pokolenie += 1
        _wpisy.clear()
        _bajty = 0
        if dane:
            _pokolenie_danych += 1
            _obiekty.clear()


# --------------------------------------------------------------------------- #
# Skladniki liczone wewnatrz zestawien
# --------------------------------------------------------------------------- #
# Sugestie i strona glowna licza ten sam komplet: kampanie, targety, frazy,
# placementy i reklamy produktow z ocena kazdej pozycji. To 80-90% czasu obu
# ekranow, a wynik zalezy tylko od rynku, okresu i czulosci - nie od limitu
# czy filtra portfolio. Trzymamy go wiec osobno i liczymy raz.

_obiekty: dict[tuple, tuple[float, int, str, object]] = {}
_blokady: dict[tuple, threading.Lock] = {}


def pamietaj(klucz: tuple, licz):
    """Wynik `licz()` zapamietany do najblizszej zmiany DANYCH.

    Decyzje (kolejka, odrzucenia) go nie kasuja - wolajacy musi je
    uwzglednic sam (patrz `_TYLKO_DECYZJE`). Zwracany obiekt jest
    WSPOLDZIELONY - nie wolno go modyfikowac. Dwa watki pytajace o to samo
    czekaja na jedno liczenie.
    """
    odcisk = ""

    def _gotowe():
        wpis = _obiekty.get(klucz)
        if (
            wpis and wpis[1] == _pokolenie_danych
            and time.monotonic() - wpis[0] <= WAZNOSC
        ):
            return wpis
        return None

    with _lock:
        wpis = _gotowe()
        if wpis:
            return wpis[3]
        blokada = _blokady.setdefault(klucz, threading.Lock())
    with blokada:
        with _lock:
            wpis = _gotowe()
            if wpis:
                return wpis[3]
            pokolenie = _pokolenie_danych
        wynik = licz()
        with _lock:
            if pokolenie == _pokolenie_danych:
                _obiekty[klucz] = (time.monotonic(), pokolenie, odcisk, wynik)
        return wynik


# --------------------------------------------------------------------------- #
# Liczenie z wyprzedzeniem
# --------------------------------------------------------------------------- #
# Po kazdym pobraniu danych pamiec jest pusta i pierwszy, kto otworzy ekran,
# czekalby na pelne przeliczenie (na duzym koncie do kilkunastu sekund).
# Dlatego panel sam "otwiera" u siebie glowne ekrany zaraz po pobraniu -
# kazdy rynek i wszystkie razem, domyslny okres 30 dni, z DOKLADNIE tymi
# parametrami, ktore wysyla przegladarka (frontend: lib/filters.tsx
# i app/*/page.tsx). Uzytkownik dostaje gotowy wynik z pamieci.
#
# Zapytania ida przez HTTP do samego siebie, zeby trafily w te sama pamiec
# co zapytania z przegladarki. Przepuszcza je guard po jednorazowym sekrecie
# znanym tylko temu procesowi - z zewnatrz nie da sie go podrobic.

SEKRET_WEWNETRZNY = secrets.token_urlsafe(32)
NAGLOWEK_WEWNETRZNY = "x-adspanel-rozgrzewka"

# Ile sekund spokoju po ostatniej zmianie danych, zanim zaczniemy. Dociaganie
# historii zwalnia blokade kilka razy z rzedu - bez tego zaczynalibysmy od
# nowa po kazdym rynku.
SPOKOJ = 60

_rozgrzewka = threading.Lock()
_rozgrzewka_znow = threading.Event()
_ostatnia_zmiana = 0.0


def czy_wewnetrzne(request: Request) -> bool:
    """Zapytanie rozgrzewki z tego samego procesu - guard je przepuszcza."""
    host = request.client.host if request.client else ""
    return (
        host in ("127.0.0.1", "::1")
        and secrets.compare_digest(
            request.headers.get(NAGLOWEK_WEWNETRZNY, ""), SEKRET_WEWNETRZNY
        )
    )


def rozgrzej_w_tle() -> None:
    global _ostatnia_zmiana
    _ostatnia_zmiana = time.monotonic()
    _rozgrzewka_znow.set()
    if _rozgrzewka.locked():
        return  # trwajaca rozgrzewka zauwazy flage i zacznie od nowa
    threading.Thread(target=_rozgrzej, name="rozgrzewka", daemon=True).start()


# --- Zakresy, ktorych uzytkownik naprawde uzywa ---------------------------
# Domyslne 30 dni to tylko punkt wyjscia: przelacznik okresu zapamietuje
# wybor w przegladarce, wiec ktos pracujacy na 60 dniach otwieralby co dzien
# ekrany niepoliczone. Zapisujemy wiec dlugosci okresow (konczacych sie
# wczoraj) z prawdziwych zapytan i rozgrzewamy te, ktorych uzyto ostatnio.
# Plik w data/, zeby przetrwal restart i nocna aktualizacje.

_ZAKRESY_PLIK = "zakresy-ekranow.json"
MAKS_ZAKRESOW = 3          # ile roznych dlugosci okresu rozgrzewamy
ZAKRES_WAZNY_DNI = 14      # nieuzywany dluzej - wypada z listy
_zakresy: dict[str, str] | None = None


def _plik_zakresow():
    from .config import PROJECT_ROOT

    return PROJECT_ROOT / "data" / _ZAKRESY_PLIK


def _wczytaj_zakresy() -> dict[str, str]:
    global _zakresy
    if _zakresy is None:
        import json

        try:
            _zakresy = dict(json.loads(_plik_zakresow().read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            _zakresy = {}
    return _zakresy


def zanotuj_zakres(start: str | None, end: str | None) -> None:
    """Dlugosc okresu z zapytania przegladarki, gdy konczy sie wczoraj."""
    import datetime as dt
    import json

    if not start or not end:
        return
    try:
        a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError:
        return
    dzis = dt.date.today()
    # Przegladarka liczy "wczoraj" w czasie polskim, serwer w UTC - po
    # polnocy przez godzine to dwa rozne dni. Oba traktujemy jako "do wczoraj".
    if b not in (dzis - dt.timedelta(days=1), dzis):
        return
    dni = (b - a).days + 1
    if not 1 <= dni <= 95:
        return
    zakresy = _wczytaj_zakresy()
    klucz, teraz = str(dni), dzis.isoformat()
    if zakresy.get(klucz) == teraz:
        return
    with _lock:
        zakresy[klucz] = teraz
    try:
        _plik_zakresow().parent.mkdir(parents=True, exist_ok=True)
        _plik_zakresow().write_text(json.dumps(zakresy), encoding="utf-8")
    except OSError:
        pass


def zakresy_do_rozgrzania() -> list[int]:
    """30 dni zawsze, do tego ostatnio uzywane dlugosci okresu."""
    import datetime as dt

    granica = (dt.date.today() - dt.timedelta(days=ZAKRES_WAZNY_DNI)).isoformat()
    uzywane = sorted(
        ((d, int(k)) for k, d in _wczytaj_zakresy().items() if d >= granica),
        reverse=True,
    )
    out: list[int] = []
    for _, dni in uzywane:
        if dni not in out:
            out.append(dni)
    if 30 not in out:
        out.append(30)
    return out[:MAKS_ZAKRESOW] if 30 in out[:MAKS_ZAKRESOW] else out[:MAKS_ZAKRESOW - 1] + [30]


def adresy_ekranow(rynki: list[int], start: str, end: str) -> list[str]:
    """Zapytania, ktore wysylaja glowne ekrany w domyslnym widoku."""
    from urllib.parse import urlencode

    def u(sciezka: str, **par) -> str:
        return sciezka + "?" + urlencode({k: v for k, v in par.items() if v is not None})

    out = [u("/api/markets", start=start, end=end)]
    for pid in [*rynki, None]:
        z = dict(profile_id=pid, start=start, end=end)
        out += [
            # Sugestie i strona glowna najpierw - najciezsze i najczesciej otwierane
            u("/api/suggestions", **z, limit=1000, sensitivity=1),
            u("/api/home/status", **z),
            u("/api/overview", **z),
            u("/api/monthly", profile_id=pid, months=6),
            u("/api/anomalies", profile_id=pid),
            u("/api/effectiveness", profile_id=pid),
            u("/api/portfolios", **z),
            u("/api/portfolios/timeseries", **z),
            u("/api/campaigns", **z, limit=1000),
            u("/api/targets", **z, min_clicks=0, limit=2000),
            u("/api/search-terms", **z, min_clicks=1, limit=3000),
            u("/api/placements", **z),
            u("/api/placements/by-campaign", **z, limit=5000),
            u("/api/placements/off-amazon", **z),
            u("/api/products", **z, limit=2000),
            u("/api/negatives/keywords", profile_id=pid, limit=20000),
            u("/api/negatives/targets", profile_id=pid, limit=20000),
            u("/api/opportunities/overlap", **z, min_clicks=10, limit=300),
            u("/api/opportunities/sku-overlap", **z, min_clicks=10, limit=300),
            u("/api/ngrams", **z, limit=300),
            u("/api/opportunities/budget-capped", **z),
        ]
    return out


def _gotowy_do_pracy() -> bool:
    """Panel z kluczami Amazona i wazna licencja - inaczej nie ma czego liczyc
    (bez licencji guard i tak odrzuci kazde zapytanie)."""
    from . import licencja
    from ._wydanie import WYMAGA_LICENCJI
    from .config import settings

    if not settings.is_configured:
        return False
    if not WYMAGA_LICENCJI:
        return True
    try:
        return licencja.stan().ok
    except Exception:  # noqa: BLE001
        return False


def _rozgrzej() -> None:
    import datetime as dt
    import os

    import httpx

    if not _gotowy_do_pracy():
        return
    if not _rozgrzewka.acquire(blocking=False):
        return
    baza = os.environ.get("PANEL_WEWNETRZNY_URL", "http://127.0.0.1:8000")
    try:
        while _rozgrzewka_znow.is_set():
            # Czekamy na spokoj: koniec pobierania i minute bez zmian
            from .etl import sync_lock

            while (
                time.monotonic() - _ostatnia_zmiana < SPOKOJ
                or sync_lock.co_trwa() is not None
            ):
                time.sleep(5)
            _rozgrzewka_znow.clear()

            from .api import queries
            from .db import session_scope

            koniec = dt.date.today() - dt.timedelta(days=1)
            try:
                with session_scope() as s:
                    rynki = [p["profile_id"] for p in queries.profiles(s)]
                adresy: list[str] = []
                # Najpierw ostatnio uzywany okres - to on bedzie otwarty
                for dni in zakresy_do_rozgrzania():
                    poczatek = koniec - dt.timedelta(days=dni - 1)
                    adresy += adresy_ekranow(rynki, poczatek.isoformat(), koniec.isoformat())
                t0 = time.monotonic()
                gotowe = 0
                with httpx.Client(
                    base_url=baza, timeout=600,
                    headers={NAGLOWEK_WEWNETRZNY: SEKRET_WEWNETRZNY},
                ) as http:
                    for adres in adresy:
                        if _rozgrzewka_znow.is_set():
                            break  # dane znow sie zmienily - od poczatku
                        try:
                            if http.get(adres).status_code == 200:
                                gotowe += 1
                        except httpx.HTTPError:
                            log.debug("Rozgrzewka: %s nie odpowiedzial", adres)
                log.info(
                    "Ekrany policzone z wyprzedzeniem: %s z %s w %.0f s",
                    gotowe, len(adresy), time.monotonic() - t0,
                )
            except Exception:  # noqa: BLE001 - to tylko przyspieszenie
                log.warning("Liczenie ekranow z wyprzedzeniem nie powiodlo sie", exc_info=True)
    finally:
        _rozgrzewka.release()


def _do_zapamietania(request: Request) -> bool:
    sciezka = request.url.path
    if request.method != "GET" or sciezka.startswith(_POMIJANE):
        return False
    # /api/campaigns/{id} to naglowek jednej kampanii - tani, ale i tak
    # zestawienie, wiec moze isc przez pamiec razem z lista.
    return sciezka.startswith(_SCIEZKI)


def _stan_decyzji() -> str:
    """Odcisk tabel, ktore zmieniaja sie poza zapytaniami HTTP (reguly w tle)."""
    from sqlalchemy import text

    from .db import engine

    with engine.connect() as conn:
        return str(conn.execute(text(
            "SELECT (SELECT count(*) || ':' || coalesce(max(created_at)::text, '') || ':' "
            "        || coalesce(max(applied_at)::text, '') || ':' "
            "        || coalesce(sum(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END), 0) "
            "        FROM pending_plans), "
            "       (SELECT count(*) || ':' || coalesce(max(id), 0) FROM dismissed_suggestions), "
            "       (SELECT coalesce(max(id), 0) FROM change_log)"
        )).one())


def _zapisz(klucz: str, pokolenie: int, odcisk: str, tresc: bytes, naglowki: dict[str, str]):
    global _bajty
    # Trzymamy skompresowane: JSON zestawien sciska sie ~10 razy, a pelny
    # komplet ekranow (kazdy rynek, dwa okresy) to kilkaset MB surowo.
    # Rozpakowanie przy odczycie to milisekundy.
    tresc = zlib.compress(tresc, 1)
    if len(tresc) > MAKS_BAJTOW // 4:
        return
    with _lock:
        if pokolenie != _pokolenie:
            return  # w trakcie liczenia cos sie zmienilo - wynik juz nieaktualny
        stary = _wpisy.pop(klucz, None)
        if stary:
            _bajty -= len(stary[3])
        _wpisy[klucz] = (time.monotonic(), pokolenie, odcisk, tresc, naglowki)
        _bajty += len(tresc)
        while _bajty > MAKS_BAJTOW and _wpisy:
            _, (_, _, _, t, _) = _wpisy.popitem(last=False)
            _bajty -= len(t)


def _odczytaj(klucz: str, odcisk: str) -> tuple[bytes, dict[str, str]] | None:
    with _lock:
        wpis = _wpisy.get(klucz)
        if not wpis:
            return None
        kiedy, pokolenie, jego_odcisk, tresc, naglowki = wpis
        if (
            pokolenie != _pokolenie
            or jego_odcisk != odcisk
            or time.monotonic() - kiedy > WAZNOSC
        ):
            return None
        _wpisy.move_to_end(klucz)
    return zlib.decompress(tresc), naglowki


async def middleware(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        odpowiedz = await call_next(request)
        # Zmiana mogla sie udac czesciowo - kasujemy niezaleznie od kodu.
        uniewaznij(
            request.url.path,
            dane=not request.url.path.startswith(_TYLKO_DECYZJE),
        )
        return odpowiedz

    if not _do_zapamietania(request):
        return await call_next(request)

    if not czy_wewnetrzne(request):
        zanotuj_zakres(request.query_params.get("start"), request.query_params.get("end"))

    try:
        odcisk = await asyncio.to_thread(_stan_decyzji)
    except Exception:  # noqa: BLE001 - bez odcisku po prostu liczymy
        log.debug("Pamiec odpowiedzi: brak odcisku", exc_info=True)
        return await call_next(request)

    # Kolejnosc parametrow bez znaczenia; dzisiejsza data w kluczu, bo
    # anomalie i skutecznosc licza okno od "dzis", a nie z parametrow.
    import datetime as dt
    from urllib.parse import parse_qsl, urlencode

    klucz = (
        request.url.path + "?" + urlencode(sorted(parse_qsl(request.url.query)))
        + "#" + dt.date.today().isoformat()
    )
    while True:
        trafienie = _odczytaj(klucz, odcisk)
        if trafienie:
            tresc, naglowki = trafienie
            return Response(tresc, status_code=200, headers={**naglowki, "X-Pamiec": "1"})
        trwa = _w_toku.get(klucz)
        if trwa is None:
            break
        try:
            await asyncio.shield(trwa)
        except Exception:  # noqa: BLE001 - nieudane liczenie: probujemy sami
            break
        if not _odczytaj(klucz, odcisk):
            break

    petla = asyncio.get_running_loop()
    moje = petla.create_future()
    _w_toku[klucz] = moje
    pokolenie = _pokolenie
    try:
        odpowiedz = await call_next(request)
        if odpowiedz.status_code != 200:
            return odpowiedz
        czesci = [c async for c in odpowiedz.body_iterator]
        tresc = b"".join(czesci)
        naglowki = {
            k: v for k, v in odpowiedz.headers.items()
            if k.lower() in ("content-type",)
        }
        _zapisz(klucz, pokolenie, odcisk, tresc, naglowki)
        return Response(tresc, status_code=200, headers=naglowki)
    finally:
        if not moje.done():
            moje.set_result(None)
        _w_toku.pop(klucz, None)

"""Ustawienia -> Konto Amazon: klucze Ads API i SP-API wpisywane z panelu.

Wczesniej klucze wpisywal administrator w pliku .env, a token generowal
skrypt w terminalu. Klient nietechniczny potrzebowal do tego informatyka,
a najczestsze bledy (zly profil, niepelny sekret, wygasly kod) konczyly sie
angielskim komunikatem Amazona. Tutaj kazdy krok jest sprawdzany od razu
i tlumaczony na zrozumialy komunikat.

Sekrety nigdy nie wracaja do przegladarki - API oddaje tylko informacje,
czy sa ustawione, i ich ostatnie znaki do rozpoznania.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
import urllib.parse
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import plik_env
from ..ads.auth import token_provider
from ..config import ADS_ENDPOINTS, LWA_TOKEN_URL, SP_ENDPOINTS, settings
from ..db import session_scope
from ..models import Profile

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ustawienia/amazon")

# Strona logowania jest regionalna; token wymieniamy zawsze na api.amazon.com
AUTH_HOSTS = {
    "NA": "https://www.amazon.com/ap/oa",
    "EU": "https://eu.account.amazon.com/ap/oa",
    "FE": "https://apac.account.amazon.com/ap/oa",
}
SCOPE = "advertising::campaign_management"
# Kod z Amazona zyje ~5 minut; stan trzymamy troche dluzej na wolne klikanie
STATE_TTL_S = 15 * 60
_stany: dict[str, float] = {}

Region = Literal["EU", "NA", "FE"]


# --------------------------------------------------------------------------- #


def _koncowka(wartosc: str, n: int = 4) -> str | None:
    return f"…{wartosc[-n:]}" if wartosc else None


def _lwa(dane: dict[str, str]) -> httpx.Response:
    return httpx.post(
        LWA_TOKEN_URL,
        data=dane,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )


def _blad_lwa(resp: httpx.Response) -> tuple[str, str]:
    """(kod bledu LWA, opis po polsku)."""
    try:
        kod = resp.json().get("error", "")
    except ValueError:
        kod = ""
    opisy = {
        "invalid_client": "Amazon nie rozpoznaje tej pary Client ID / Client Secret. "
        "Sprawdź, czy oba pochodzą z tego samego Security Profile i czy przy "
        "kopiowaniu nie ucięły się znaki.",
        "invalid_grant": "Kod z Amazona wygasł albo został już użyty (ważny ok. 5 minut "
        "i tylko raz). Kliknij „Połącz z Amazonem” jeszcze raz.",
        "unauthorized_client": "Ta aplikacja nie ma uprawnień do tego rodzaju logowania. "
        "Sprawdź, czy klucze pochodzą z Security Profile zatwierdzonego do Ads API.",
        "invalid_request": "Amazon odrzucił żądanie. Najczęściej adres zwrotny w panelu "
        "różni się od wpisanego w „Allowed Return URLs” Security Profile.",
    }
    return kod, opisy.get(kod, f"Amazon odrzucił żądanie (HTTP {resp.status_code}).")


def _sprawdz_pare(client_id: str, client_secret: str, redirect: str) -> None:
    """Weryfikuje Client ID + Secret bez logowania uzytkownika.

    Wymiana nieistniejacego kodu: przy zlej parze kluczy Amazon odpowiada
    invalid_client, przy dobrej - invalid_grant (zly kod). Tak odrozniamy
    literowke w sekrecie od poprawnych kluczy, zanim ktos przejdzie przez
    cale logowanie.
    """
    resp = _lwa({
        "grant_type": "authorization_code",
        "code": "sprawdzenie-kluczy",
        "redirect_uri": redirect,
        "client_id": client_id,
        "client_secret": client_secret,
    })
    kod, opis = _blad_lwa(resp)
    if kod == "invalid_client":
        raise HTTPException(400, opis)


def _profile() -> list[dict[str, Any]]:
    with session_scope() as s:
        wiersze = s.execute(
            select(Profile.country_code, Profile.account_name).where(Profile.is_active.is_(True))
        ).all()
    return [{"kraj": k, "konto": n} for k, n in sorted(wiersze, key=lambda w: w[0] or "")]


# --------------------------------------------------------------------------- #
# Stan
# --------------------------------------------------------------------------- #


@router.get("")
def stan() -> dict[str, Any]:
    return {
        "ads": {
            "client_id": settings.ads_client_id or None,
            "client_secret": _koncowka(settings.ads_client_secret),
            "polaczone": bool(settings.ads_refresh_token),
            "region": settings.ads_region.upper(),
            "redirect_uri": settings.ads_redirect_uri,
            "profile": _profile(),
        },
        "sp": {
            "client_id": settings.sp_client_id or None,
            "client_secret": _koncowka(settings.sp_client_secret),
            "refresh_token": _koncowka(settings.sp_refresh_token, 6),
            "region": settings.sp_region.upper(),
        },
    }


# --------------------------------------------------------------------------- #
# Amazon Ads API
# --------------------------------------------------------------------------- #


class AdsKlucze(BaseModel):
    client_id: str
    # Puste = zostaw obecny (formularz nie zna zapisanego sekretu)
    client_secret: str = ""
    region: Region = "EU"
    redirect_uri: str = ""


@router.put("/ads")
def zapisz_ads(body: AdsKlucze) -> dict[str, Any]:
    client_id = body.client_id.strip()
    sekret = body.client_secret.strip() or settings.ads_client_secret
    redirect = body.redirect_uri.strip() or settings.ads_redirect_uri

    if not client_id.startswith("amzn1.application-oa2-client."):
        raise HTTPException(400, "Client ID powinien zaczynać się od „amzn1.application-oa2-client.”")
    if not sekret:
        raise HTTPException(400, "Podaj Client Secret.")
    if body.region not in ADS_ENDPOINTS:
        raise HTTPException(400, "Nieznany region.")

    _sprawdz_pare(client_id, sekret, redirect)

    zmiany = {
        "ADS_CLIENT_ID": client_id,
        "ADS_CLIENT_SECRET": sekret,
        "ADS_REGION": body.region,
        "ADS_REDIRECT_URI": redirect,
    }
    # Nowe klucze aplikacji uniewazniaja token wystawiony dla poprzednich
    if client_id != settings.ads_client_id:
        zmiany["ADS_REFRESH_TOKEN"] = ""
    plik_env.zapisz(zmiany)
    _odswiez_token_provider()
    log.info("Zapisano klucze Ads API z panelu (Client ID …%s)", client_id[-6:])
    return stan()


@router.get("/ads/link")
def link_logowania() -> dict[str, str]:
    if not (settings.ads_client_id and settings.ads_client_secret):
        raise HTTPException(400, "Najpierw zapisz Client ID i Client Secret.")
    teraz = time.time()
    for s, t in list(_stany.items()):
        if teraz - t > STATE_TTL_S:
            del _stany[s]
    state = secrets.token_urlsafe(16)
    _stany[state] = teraz

    params = {
        "client_id": settings.ads_client_id,
        "scope": SCOPE,
        "response_type": "code",
        "redirect_uri": settings.ads_redirect_uri,
        "state": state,
    }
    host = AUTH_HOSTS.get(settings.ads_region.upper(), AUTH_HOSTS["EU"])
    return {"url": f"{host}?{urllib.parse.urlencode(params)}"}


class AdsKod(BaseModel):
    adres: str


@router.post("/ads/polacz")
def polacz(body: AdsKod) -> dict[str, Any]:
    surowy = body.adres.strip().strip('"').strip("'")
    query = urllib.parse.urlparse(surowy).query if "?" in surowy else ""
    parametry = urllib.parse.parse_qs(query)

    if "error" in parametry:
        # Amazon wraca na adres zwrotny z bledem zamiast kodu - np. gdy
        # uzytkownik kliknal "Cancel" albo profil nie ma dostepu do Ads API
        opis = parametry.get("error_description", parametry["error"])[0]
        if "scope" in opis.lower():
            # Najczestszy blad przy wdrozeniu - sprawdzone na koncie Murando
            raise HTTPException(400, "Ten Client ID nie ma dostępu do Amazon Ads API (Amazon: "
                                f"„{opis}”). Klucze pochodzą z innego Security Profile niż ten "
                                "zatwierdzony do Ads API – wpisz klucze z właściwego profilu.")
        raise HTTPException(400, f"Amazon nie udzielił dostępu: {opis}")

    kod = parametry.get("code", [""])[0] if parametry else surowy
    if not kod:
        raise HTTPException(400, "W wklejonym adresie nie ma kodu z Amazona. Skopiuj cały adres z paska przeglądarki.")

    state = parametry.get("state", [""])[0]
    if state and state not in _stany:
        raise HTTPException(400, "Ten adres pochodzi z wcześniejszej próby. Kliknij „Połącz z Amazonem” jeszcze raz.")
    _stany.pop(state, None)

    resp = _lwa({
        "grant_type": "authorization_code",
        "code": kod,
        "redirect_uri": settings.ads_redirect_uri,
        "client_id": settings.ads_client_id,
        "client_secret": settings.ads_client_secret,
    })
    if resp.status_code != 200:
        raise HTTPException(400, _blad_lwa(resp)[1])
    refresh = resp.json().get("refresh_token")
    if not refresh:
        raise HTTPException(502, "Amazon nie zwrócił tokenu. Spróbuj ponownie.")

    plik_env.zapisz({"ADS_REFRESH_TOKEN": refresh})
    _odswiez_token_provider()
    log.info("Polaczono konto Amazon Ads z panelu")
    test = _test_ads()
    if test["ok"] and _pierwsze_pobranie():
        test["opis"] = (test["opis"].split(". Dane")[0] + ". Pobieram dane z ostatnich 30 dni – "
                        "potrwa to od kilkunastu minut do kilku godzin, panel można w tym czasie zamknąć.")
    return {**stan(), "test": test}


def _pierwsze_pobranie() -> bool:
    """Po pierwszym polaczeniu pobiera od razu 30 dni historii.

    Bez tego nowy klient czekalby na planowe odswiezanie, ktore bierze tylko
    3 dni wstecz - pulpit bylby prawie pusty. Uruchamiamy wylacznie przy
    pustej bazie: ponowne polaczenie (np. nowy token) nie zamawia raportow.
    """
    from sqlalchemy import func

    from ..etl import sync, sync_lock
    from ..models import CampaignDaily

    with session_scope() as s:
        if s.execute(select(func.count()).select_from(CampaignDaily)).scalar():
            return False

    try:
        sync_lock.zajmij("pierwsze pobranie po polaczeniu konta")
    except sync_lock.Zajete:
        return False

    def _w_tle() -> None:
        try:
            sync.sync_all(lookback_days=30, with_reports=True)
        except Exception:  # noqa: BLE001 - watek w tle, blad tylko do logu
            log.exception("Pierwsze pobranie po polaczeniu konta nie powiodlo sie")
        finally:
            sync_lock.zwolnij()

    threading.Thread(target=_w_tle, name="pierwsze-pobranie", daemon=True).start()
    log.info("Pierwsze pobranie danych (30 dni) uruchomione po polaczeniu konta")
    return True


@router.post("/ads/test")
def test_ads() -> dict[str, Any]:
    return {**stan(), "test": _test_ads()}


def _test_ads() -> dict[str, Any]:
    """Pobiera profile reklamowe - najprostszy dowod, ze wszystko dziala."""
    from ..etl import sync  # ciezki import tylko gdy potrzebny

    try:
        profile = sync.sync_profiles()
    except Exception as exc:  # noqa: BLE001 - komunikat idzie do uzytkownika
        log.warning("Test Ads API nieudany: %s", exc)
        tekst = str(exc)
        if "401" in tekst or "LWA" in tekst:
            return {"ok": False, "opis": "Token został odrzucony. Połącz konto ponownie."}
        return {"ok": False, "opis": f"Nie udało się pobrać profili: {tekst[:200]}"}
    if not profile:
        return {"ok": False, "opis": "Połączenie działa, ale to konto Amazon nie ma żadnych profili reklamowych. "
                "Zaloguj się na konto z dostępem do reklam firmy."}
    return {"ok": True, "opis": f"Połączono – profili reklamowych: {len(profile)}. Dane pobierają się "
            "same co kilka godzin; od razu – przycisk „Sync” u góry panelu."}


def _odswiez_token_provider() -> None:
    # Singleton zapamietuje klucze przy imporcie - po zmianie trzeba je podmienic
    token_provider.client_id = settings.ads_client_id
    token_provider.client_secret = settings.ads_client_secret
    token_provider.refresh_token = settings.ads_refresh_token
    token_provider.invalidate()


# --------------------------------------------------------------------------- #
# Selling Partner API
# --------------------------------------------------------------------------- #


class SpKlucze(BaseModel):
    client_id: str
    client_secret: str = ""
    refresh_token: str = ""
    region: Region = "EU"


@router.put("/sp")
def zapisz_sp(body: SpKlucze) -> dict[str, Any]:
    client_id = body.client_id.strip()
    sekret = body.client_secret.strip() or settings.sp_client_secret
    refresh = body.refresh_token.strip() or settings.sp_refresh_token

    if not client_id.startswith("amzn1.application-oa2-client."):
        raise HTTPException(400, "Client ID powinien zaczynać się od „amzn1.application-oa2-client.”")
    if not sekret:
        raise HTTPException(400, "Podaj Client Secret.")
    if not refresh.startswith("Atzr|"):
        raise HTTPException(400, "Refresh Token powinien zaczynać się od „Atzr|” (Seller Central → Develop Apps → Authorize).")
    if body.region not in SP_ENDPOINTS:
        raise HTTPException(400, "Nieznany region.")

    # Sprawdzenie przed zapisem: odswiezenie tokenu potwierdza cala trojke
    resp = _lwa({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": client_id,
        "client_secret": sekret,
    })
    if resp.status_code != 200:
        kod, opis = _blad_lwa(resp)
        if kod == "invalid_grant":
            opis = ("Refresh Token nie pasuje do tej aplikacji albo został cofnięty. "
                    "Wygeneruj nowy w Seller Central (Develop Apps → Authorize).")
        raise HTTPException(400, opis)

    plik_env.zapisz({
        "SP_CLIENT_ID": client_id,
        "SP_CLIENT_SECRET": sekret,
        "SP_REFRESH_TOKEN": refresh,
        "SP_REGION": body.region,
    })
    log.info("Zapisano klucze SP-API z panelu (Client ID …%s)", client_id[-6:])
    return {**stan(), "test": _test_sp()}


@router.post("/sp/test")
def test_sp() -> dict[str, Any]:
    return {**stan(), "test": _test_sp()}


def _test_sp() -> dict[str, Any]:
    from ..sp.client import check_access

    wynik = check_access()
    if not wynik.get("auth"):
        return {"ok": False, "opis": f"Klucze odrzucone: {wynik.get('problem', 'nieznany błąd')}"}
    if not wynik.get("sales_report"):
        return {"ok": False, "opis": f"Połączono (rynki: {wynik.get('marketplaces', 0)}), ale brak dostępu "
                "do raportu sprzedaży – aplikacja potrzebuje roli Brand Analytics."}
    return {"ok": True, "opis": f"Połączono – rynki: {wynik.get('marketplaces', 0)}, raport sprzedaży dostępny."}

"""Generator refresh tokena do Amazon Ads API.

Uruchom i postepuj wedlug instrukcji na ekranie:

    python token-amazon-ads.py

Do dzialania wystarczy Python 3.9 lub nowszy. Skrypt nie instaluje niczego
i nie korzysta z zadnych bibliotek spoza standardowej biblioteki Pythona -
mozna go przeczytac w calosci przed uruchomieniem.

Co robi:
  1. pyta o Client ID i Client Secret z Twojego Security Profile,
  2. otwiera przegladarke z ekranem zgody Amazona,
  3. odbiera kod z przekierowania na localhost,
  4. wymienia go na refresh token i wypisuje na ekranie.

Czego NIE robi: nie wysyla niczego poza Amazona, nie zapisuje danych na dysku,
nie laczy sie z zadnym innym serwerem. Jedyne polaczenia wychodzace ida na
amazon.com.

UWAGA: refresh token to klucz dostepu do konta reklamowego. Przekazuj go
menedzerem hasel albo wiadomoscia, ktora da sie usunac - nie zwyklym mailem.
"""
from __future__ import annotations

import getpass
import http.server
import json
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# Strona logowania jest regionalna, token wymienia sie zawsze na api.amazon.com
STRONY_LOGOWANIA = {
    "EU": "https://eu.account.amazon.com/ap/oa",
    "NA": "https://www.amazon.com/ap/oa",
    "FE": "https://apac.account.amazon.com/ap/oa",
}
URL_TOKENA = "https://api.amazon.com/auth/o2/token"
ZAKRES = "advertising::campaign_management"

# Musi byc IDENTYCZNY z wpisem w Allowed Return URLs w Security Profile.
PRZEKIEROWANIE = "http://localhost:9090/redirect"
PORT = 9090
CZEKAJ_SEKUND = 300

STRONA_OK = """<!doctype html><html lang="pl"><meta charset="utf-8">
<title>Gotowe</title>
<body style="font-family:system-ui,sans-serif;max-width:34rem;margin:4rem auto;
             line-height:1.6;color:#1b1d21">
<h2 style="margin:0 0 .5rem">Gotowe</h2>
<p>Autoryzacja zakonczona. Wroc do okna terminala - tam czeka token.</p>
<p style="color:#6e7076;font-size:.9rem">To okno mozna zamknac.</p>
</body></html>"""


class _Odbiornik(http.server.BaseHTTPRequestHandler):
    """Lokalny serwer, ktory przechwytuje kod z przekierowania Amazona."""

    wynik: dict[str, str] = {}

    def do_GET(self):  # noqa: N802 - nazwa wymagana przez biblioteke
        czesci = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(czesci.query)

        if czesci.path != "/redirect":
            self.send_response(404)
            self.end_headers()
            return

        if params.get("code"):
            _Odbiornik.wynik = {"code": params["code"][0]}
            tresc = STRONA_OK.encode("utf-8")
        else:
            blad = params.get("error_description", params.get("error", ["nieznany"]))[0]
            _Odbiornik.wynik = {"error": blad}
            tresc = f"<h2>Blad autoryzacji</h2><p>{blad}</p>".encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(tresc)))
        self.end_headers()
        self.wfile.write(tresc)

    def log_message(self, *_args):
        pass  # bez zasmiecania ekranu logami HTTP


def _kod_z_adresu(surowy: str) -> str:
    """Wyciaga kod z calego adresu albo przyjmuje sam kod."""
    surowy = surowy.strip().strip('"').strip("'")
    if "code=" not in surowy:
        return surowy
    zapytanie = urllib.parse.urlparse(surowy).query or surowy.split("?", 1)[-1]
    kod = urllib.parse.parse_qs(zapytanie).get("code", [""])[0]
    if not kod:
        raise SystemExit("Nie znalazlem parametru `code` w podanym adresie.")
    return kod


def _wymien(kod: str, client_id: str, client_secret: str) -> str:
    dane = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": kod,
        "redirect_uri": PRZEKIEROWANIE,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")

    zadanie = urllib.request.Request(
        URL_TOKENA, data=dane,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(zadanie, timeout=40) as odp:
            wynik = json.loads(odp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        tresc = exc.read().decode("utf-8", "replace")
        print(f"\nAmazon odrzucil zadanie (HTTP {exc.code}):\n{tresc}\n")
        print("Najczestsze przyczyny:")
        print("  * kod stracil waznosc - jest jednorazowy i zyje okolo 5 minut,")
        print(f"  * adres zwrotny w Security Profile rozni sie od: {PRZEKIEROWANIE}")
        print("  * Client Secret zostal wklejony niekompletnie.")
        raise SystemExit(1) from None

    token = wynik.get("refresh_token")
    if not token:
        raise SystemExit(f"Odpowiedz bez refresh_token: {wynik}")
    return token


def _port_wolny() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) != 0


def main() -> int:
    print("=" * 68)
    print("  Generator refresh tokena do Amazon Ads API")
    print("=" * 68)
    print()
    print("Potrzebne beda dwie wartosci z Security Profile (Login with Amazon):")
    print("  developer.amazon.com -> Login with Amazon -> Security Profile")
    print("                       -> Web Settings")
    print()
    print(f"W polu Allowed Return URLs musi byc DOKLADNIE: {PRZEKIEROWANIE}")
    print()

    client_id = input("Client ID: ").strip()
    if not client_id:
        raise SystemExit("Client ID jest wymagany.")
    # getpass nie pokazuje wpisywanych znakow - sekret nie zostaje na ekranie
    # ani w historii terminala.
    client_secret = getpass.getpass("Client Secret (nie bedzie widoczny): ").strip()
    if not client_secret:
        raise SystemExit("Client Secret jest wymagany.")

    region = (input("Region [EU]: ").strip().upper() or "EU")
    if region not in STRONY_LOGOWANIA:
        raise SystemExit(f"Nieznany region. Dozwolone: {list(STRONY_LOGOWANIA)}")

    stan = secrets.token_urlsafe(16)
    link = f"{STRONY_LOGOWANIA[region]}?" + urllib.parse.urlencode({
        "client_id": client_id,
        "scope": ZAKRES,
        "response_type": "code",
        "redirect_uri": PRZEKIEROWANIE,
        "state": stan,
    })

    print()
    print("-" * 68)
    print("Otworze teraz przegladarke. Zaloguj sie na konto Amazon, ktore ma")
    print("byc podlaczone, i potwierdz zgode.")
    print()
    print("Gdyby okno sie nie otworzylo, wklej ten adres recznie:")
    print()
    print(link)
    print("-" * 68)
    print()

    kod = ""
    if _port_wolny():
        serwer = http.server.HTTPServer(("127.0.0.1", PORT), _Odbiornik)
        serwer.timeout = CZEKAJ_SEKUND
        watek = threading.Thread(target=serwer.handle_request, daemon=True)
        watek.start()
        try:
            webbrowser.open(link)
        except Exception:  # noqa: BLE001 - brak przegladarki nie jest bledem
            pass
        print(f"Czekam na potwierdzenie w przegladarce (do {CZEKAJ_SEKUND // 60} min)...")
        watek.join(timeout=CZEKAJ_SEKUND)
        serwer.server_close()

        if _Odbiornik.wynik.get("error"):
            raise SystemExit(f"Amazon zwrocil blad: {_Odbiornik.wynik['error']}")
        kod = _Odbiornik.wynik.get("code", "")
    else:
        print(f"UWAGA: port {PORT} jest zajety, wiec nie moge odebrac kodu automatycznie.")

    if not kod:
        print()
        print("Skopiuj CALY adres z paska przegladarki po potwierdzeniu zgody.")
        print("Strona sie nie zaladuje - tak ma byc, liczy sie tylko adres.")
        print()
        kod = _kod_z_adresu(input("Adres z paska przegladarki: "))

    print()
    print("Wymieniam kod na token...")
    token = _wymien(kod, client_id, client_secret)

    print()
    print("=" * 68)
    print("  REFRESH TOKEN - skopiuj cala linie ponizej")
    print("=" * 68)
    print()
    print(token)
    print()
    print("=" * 68)
    print("To haslo do Twojego konta reklamowego. Przekaz je menedzerem hasel")
    print("albo wiadomoscia, ktora da sie pozniej usunac - nie zwyklym mailem.")
    print("Token zostaje wazny do zmiany hasla albo cofniecia dostepu aplikacji.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nPrzerwane.")
        sys.exit(1)

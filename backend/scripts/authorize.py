"""Generowanie refresh tokena do Amazon Ads API.

client_id i client_secret pochodza z Security Profile (Login with Amazon).
Refresh token nie lezy nigdzie gotowy - powstaje raz, przez zalogowanie
w przegladarce.

Podstawowe uzycie - jedna komenda, reszta dzieje sie sama:

    python backend/scripts/authorize.py login

Skrypt podnosi lokalny serwer na porcie z ADS_REDIRECT_URI, otwiera przegladarke,
przechwytuje kod z przekierowania, wymienia go na refresh token i zapisuje w .env.

Gdyby cos nie zadzialalo (port zajety, przegladarka na innej maszynie), jest
tryb reczny:

    python backend/scripts/authorize.py url
    python backend/scripts/authorize.py token --code "<adres z paska przegladarki>"
"""
from __future__ import annotations

import argparse
import http.server
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

# Strona logowania jest regionalna; token wymieniamy zawsze na api.amazon.com
AUTH_HOSTS = {
    "NA": "https://www.amazon.com/ap/oa",
    "EU": "https://eu.account.amazon.com/ap/oa",
    "FE": "https://apac.account.amazon.com/ap/oa",
}
TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SCOPE = "advertising::campaign_management"
WAIT_SECONDS = 300

DEFAULT_REDIRECT = "http://localhost:9090/redirect"


# --------------------------------------------------------------------------- #
# .env
# --------------------------------------------------------------------------- #


def _env_path() -> Path:
    return PROJECT_ROOT / ".env"


def _load_env() -> dict[str, str]:
    path = _env_path()
    if not path.exists():
        print(f"Brak pliku {path}")
        print("Skopiuj .env.example do .env i uzupelnij dane z Security Profile.")
        raise SystemExit(1)

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env(key: str, value: str) -> None:
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _credentials(args: argparse.Namespace) -> tuple[str, str, str, str]:
    env = _load_env()
    client_id = getattr(args, "client_id", None) or env.get("ADS_CLIENT_ID", "")
    client_secret = getattr(args, "client_secret", None) or env.get("ADS_CLIENT_SECRET", "")
    redirect = getattr(args, "redirect", None) or env.get("ADS_REDIRECT_URI", DEFAULT_REDIRECT)
    region = (getattr(args, "region", None) or env.get("ADS_REGION", "EU")).upper()

    problems = []
    if not client_id or "xxxx" in client_id:
        problems.append("ADS_CLIENT_ID")
    if not client_secret or "xxxx" in client_secret or client_secret.startswith("<"):
        problems.append("ADS_CLIENT_SECRET")

    if problems:
        print(f"Uzupelnij w pliku .env: {', '.join(problems)}")
        if "ADS_CLIENT_SECRET" in problems:
            print("\nSekret wklej bezposrednio do pliku - nie przesylaj go czatem ani mailem.")
        raise SystemExit(1)

    if region not in AUTH_HOSTS:
        print(f"Nieznany region '{region}'. Dozwolone: {', '.join(AUTH_HOSTS)}")
        raise SystemExit(1)

    return client_id, client_secret, redirect, region


def _auth_url(client_id: str, redirect: str, region: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "scope": SCOPE,
        "response_type": "code",
        "redirect_uri": redirect,
        "state": state,
    }
    return f"{AUTH_HOSTS[region]}?{urllib.parse.urlencode(params)}"


def _extract_code(raw: str) -> str:
    """Przyjmuje sam kod albo caly adres przekierowania."""
    raw = raw.strip().strip('"').strip("'")
    if "code=" not in raw:
        return raw
    query = urllib.parse.urlparse(raw).query or raw.split("?", 1)[-1]
    code = urllib.parse.parse_qs(query).get("code", [""])[0]
    if not code:
        raise SystemExit("Nie znalazlem parametru `code` w podanym adresie.")
    return code


# --------------------------------------------------------------------------- #
# Wymiana kodu na token
# --------------------------------------------------------------------------- #


def _exchange(code: str, client_id: str, client_secret: str, redirect: str) -> int:
    print("Wymieniam kod na refresh token...")
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )

    if resp.status_code != 200:
        print(f"\nAmazon odrzucil zadanie (HTTP {resp.status_code}):")
        print(resp.text[:600])
        print("\nNajczestsze przyczyny:")
        print("  * kod jest jednorazowy i wazny ~5 minut - uruchom komende ponownie")
        print(f"  * redirect_uri musi byc IDENTYCZNY z zarejestrowanym: {redirect}")
        print("  * client_secret moze byc niepelny (uwaga na spacje przy kopiowaniu)")
        return 1

    refresh = resp.json().get("refresh_token")
    if not refresh:
        print("Odpowiedz nie zawiera refresh_token:", resp.json())
        return 1

    _write_env("ADS_REFRESH_TOKEN", refresh)
    print("\nRefresh token zapisany w .env")
    print(f"  {refresh[:16]}...{refresh[-6:]}")
    print("\nNastepny krok:")
    print("  python backend/scripts/cli.py health")
    return 0


# --------------------------------------------------------------------------- #
# Tryb automatyczny: lokalny serwer przechwytuje przekierowanie
# --------------------------------------------------------------------------- #

PAGE = """<!doctype html><html lang="pl"><meta charset="utf-8">
<title>AdsPanel</title>
<style>
 body{{margin:0;height:100vh;display:grid;place-items:center;background:#12171e;
      color:#e4e9f0;font:16px/1.6 system-ui,sans-serif}}
 .box{{max-width:26rem;padding:2rem;text-align:center}}
 h1{{font-size:1.25rem;margin:0 0 .5rem;color:{color}}}
 p{{margin:0;color:#9aa5b4;font-size:.9rem}}
</style>
<div class="box"><h1>{title}</h1><p>{body}</p></div>"""


class _Catcher(http.server.BaseHTTPRequestHandler):
    """Odbiera jedno przekierowanie z Amazona i zapamietuje kod."""

    expected_path = "/redirect"
    expected_state = ""
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - nazwa wymagana przez biblioteke
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.expected_path:
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [""])[0]
        error = params.get("error_description", params.get("error", [""]))[0]
        state = params.get("state", [""])[0]

        if state != self.expected_state:
            # Odpowiedz nie pochodzi z zadania, ktore wlasnie rozpoczelismy
            error = "Niezgodny parametr state - autoryzacja przerwana."
            code = ""

        if code:
            _Catcher.result = {"code": code}
            page = PAGE.format(
                color="#5cb890",
                title="Gotowe",
                body="Mozesz zamknac te karte i wrocic do terminala.",
            )
        else:
            _Catcher.result = {"error": error or "Brak kodu w odpowiedzi."}
            page = PAGE.format(
                color="#f2637a",
                title="Autoryzacja nieudana",
                body=error or "Amazon nie zwrocil kodu.",
            )

        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass  # cisza - komunikaty prowadzi skrypt, nie serwer


def cmd_login(args: argparse.Namespace) -> int:
    client_id, client_secret, redirect, region = _credentials(args)

    parsed = urllib.parse.urlparse(redirect)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"

    if host not in ("localhost", "127.0.0.1"):
        print(f"Adres zwrotny wskazuje na {host}, nie na ten komputer.")
        print("Tryb automatyczny dziala tylko z localhost. Uzyj trybu recznego:")
        print("  python backend/scripts/authorize.py url")
        return 1

    state = secrets.token_urlsafe(24)
    _Catcher.expected_path = path
    _Catcher.expected_state = state
    _Catcher.result = {}

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), _Catcher)
    except OSError as exc:
        print(f"Nie moge nasluchiwac na porcie {port}: {exc}")
        print("Zamknij program zajmujacy ten port albo uzyj trybu recznego:")
        print("  python backend/scripts/authorize.py url")
        return 1

    server.timeout = WAIT_SECONDS
    url = _auth_url(client_id, redirect, region, state)

    print(f"Nasluchuje na {redirect}")
    print("\nOtwieram przegladarke. Zaloguj sie kontem, ktore ma dostep do konta")
    print("reklamowego, i zatwierdz dostep.")
    print(f"\nGdyby okno sie nie otworzylo, wejdz recznie:\n\n  {url}\n")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    # Przegladarka moze poprosic tez o /favicon.ico - obslugujemy zadania,
    # dopoki nie dostaniemy wlasciwego przekierowania albo nie minie limit czasu
    deadline = threading.Event()
    threading.Timer(WAIT_SECONDS, deadline.set).start()

    while not _Catcher.result and not deadline.is_set():
        server.handle_request()

    server.server_close()

    if not _Catcher.result:
        print(f"\nUplynelo {WAIT_SECONDS} s bez odpowiedzi. Sprobuj jeszcze raz.")
        return 1

    if "error" in _Catcher.result:
        print(f"\nAutoryzacja nieudana: {_Catcher.result['error']}")
        return 1

    print("Kod odebrany.")
    return _exchange(_Catcher.result["code"], client_id, client_secret, redirect)


# --------------------------------------------------------------------------- #
# Tryb reczny
# --------------------------------------------------------------------------- #


def cmd_url(args: argparse.Namespace) -> int:
    env = _load_env()
    client_id = args.client_id or env.get("ADS_CLIENT_ID", "")
    redirect = args.redirect or env.get("ADS_REDIRECT_URI", DEFAULT_REDIRECT)
    region = (args.region or env.get("ADS_REGION", "EU")).upper()

    if not client_id or "xxxx" in client_id:
        print("Uzupelnij najpierw ADS_CLIENT_ID w pliku .env")
        return 1

    print("Otworz ten adres w przegladarce i zatwierdz dostep:\n")
    print(f"  {_auth_url(client_id, redirect, region, 'manual')}\n")
    print("Po zatwierdzeniu przegladarka przekieruje na adres zwrotny. Strona moze")
    print("sie nie zaladowac - to normalne. Skopiuj CALY adres z paska i uruchom:\n")
    print('  python backend/scripts/authorize.py token --code "<wklejony adres>"')
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    client_id, client_secret, redirect, _ = _credentials(args)
    return _exchange(_extract_code(args.code), client_id, client_secret, redirect)


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(prog="authorize")
    sub = parser.add_subparsers(dest="command")

    p_login = sub.add_parser("login", help="pelna autoryzacja w jednym kroku (zalecane)")
    p_login.add_argument("--client-id")
    p_login.add_argument("--client-secret")
    p_login.add_argument("--redirect")
    p_login.add_argument("--region")
    p_login.set_defaults(func=cmd_login)

    p_url = sub.add_parser("url", help="tryb reczny: wypisz link do autoryzacji")
    p_url.add_argument("--client-id")
    p_url.add_argument("--redirect")
    p_url.add_argument("--region")
    p_url.set_defaults(func=cmd_url)

    p_token = sub.add_parser("token", help="tryb reczny: wymien kod na refresh token")
    p_token.add_argument("--code", required=True, help="kod lub caly adres przekierowania")
    p_token.add_argument("--client-id")
    p_token.add_argument("--client-secret")
    p_token.add_argument("--redirect")
    p_token.set_defaults(func=cmd_token)

    args = parser.parse_args()
    if not args.command:
        args = parser.parse_args(["login"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

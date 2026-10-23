"""Wydawanie kluczy licencyjnych AdsPanelu.

Narzedzie WYLACZNIE dla autora panelu - nie trafia do obrazu Docker ani do
paczki dla klienta (patrz .dockerignore). Klucz prywatny lezy poza
repozytorium; kto go ma, ten wystawi licencje na dowolny termin.

Jednorazowo, przy pierwszym uzyciu:

    python narzedzia/licencja.py klucze

Wypisze klucz publiczny do wklejenia w backend/app/licencja.py.

Licencja dla klienta:

    python narzedzia/licencja.py wydaj --klient "Murando" --do 2027-10-31
    python narzedzia/licencja.py wydaj --klient "Murando" --do 2027-10-31 --konto A1B2C3D4E5F6G7

--konto przypina licencje do kont reklamowych Amazon klienta (ID widac
w panelu: Ustawienia -> Amazon Ads API -> ID konta).

Sprawdzenie wydanego klucza:

    python narzedzia/licencja.py sprawdz "<klucz>"
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Poza repozytorium celowo: `git add -A` nie ma szans go zlapac.
KLUCZ_PRYWATNY = Path(r"D:\Claude Projekty\AdsPanel-KLUCZ-PRYWATNY\licencja_prywatny.pem")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def cmd_klucze(_: argparse.Namespace) -> int:
    if KLUCZ_PRYWATNY.exists():
        # Nadpisanie uniewaznia kazda wydana dotad licencje - nie robimy
        # tego przypadkiem.
        print(f"Klucz juz istnieje: {KLUCZ_PRYWATNY}")
        print("Nowy uniewaznilby wszystkie wydane licencje. Usun stary recznie, jesli naprawde trzeba.")
        return 1

    prywatny = Ed25519PrivateKey.generate()
    KLUCZ_PRYWATNY.parent.mkdir(parents=True, exist_ok=True)
    KLUCZ_PRYWATNY.write_bytes(
        prywatny.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    publiczny = prywatny.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    print(f"Klucz prywatny zapisany: {KLUCZ_PRYWATNY}")
    print("Zrob jego kopie (np. na pendrive). Bez niego nie przedluzysz zadnej licencji.")
    print()
    print("Klucz publiczny do backend/app/licencja.py (KLUCZ_PUBLICZNY):")
    print(_b64(publiczny))
    return 0


def cmd_wydaj(args: argparse.Namespace) -> int:
    if not KLUCZ_PRYWATNY.exists():
        print(f"Brak klucza prywatnego: {KLUCZ_PRYWATNY}. Najpierw: licencja.py klucze")
        return 1
    try:
        wazna_do = dt.date.fromisoformat(args.do)
    except ValueError:
        print("Data w formacie RRRR-MM-DD, np. 2027-10-31")
        return 1

    prywatny = serialization.load_pem_private_key(KLUCZ_PRYWATNY.read_bytes(), password=None)
    dane = {"klient": args.klient, "wazna_do": wazna_do.isoformat(), "wydana": dt.date.today().isoformat()}
    konta = [k.strip() for k in (args.konto or []) if k.strip()]
    if konta:
        # Przypiecie do kont Amazon klienta (ID konta z Ustawien panelu)
        dane["konta"] = konta
    tresc = json.dumps(dane, ensure_ascii=False, separators=(",", ":")).encode()
    klucz = f"{_b64(tresc)}.{_b64(prywatny.sign(tresc))}"

    print(f"Licencja: {args.klient}, wazna do {wazna_do.isoformat()} wlacznie"
          + (f", konta Amazon: {', '.join(konta)}" if konta else ", bez przypiecia do konta Amazon"))
    print()
    print(f"LICENCJA={klucz}")
    return 0


def cmd_sprawdz(args: argparse.Namespace) -> int:
    from app import licencja

    stan = licencja.sprawdz(args.klucz)
    print(json.dumps(stan.jako_slownik(), ensure_ascii=False, indent=2))
    return 0 if stan.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="licencja")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("klucze", help="jednorazowo: para kluczy do podpisywania licencji")
    p = sub.add_parser("wydaj", help="wystawia licencje dla klienta")
    p.add_argument("--klient", required=True)
    p.add_argument("--do", required=True, help="ostatni dzien waznosci, RRRR-MM-DD")
    p.add_argument("--konto", action="append", help="ID konta Amazon klienta (Ustawienia panelu); mozna kilka razy")
    p = sub.add_parser("sprawdz", help="sprawdza wydany klucz")
    p.add_argument("klucz")
    args = parser.parse_args()
    return {"klucze": cmd_klucze, "wydaj": cmd_wydaj, "sprawdz": cmd_sprawdz}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI panelu.

Przyklady:
    python backend/scripts/cli.py health
    python backend/scripts/cli.py profiles
    python backend/scripts/cli.py sync --lookback 30
    python backend/scripts/cli.py sync --profile 1234567890 --no-reports
    python backend/scripts/cli.py reports --profile 1234567890 --start 2026-07-01 --end 2026-07-31
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.etl import sync  # noqa: E402


def _date(text: str | None) -> dt.date | None:
    return dt.datetime.strptime(text, "%Y-%m-%d").date() if text else None


def _coverage(days: int) -> int:
    """Pokrycie danymi per rynek i typ raportu.

    Sluzy do wylapania dziur po nieudanym pobieraniu. Dzien bez wierszy nie
    zawsze oznacza blad - na malym rynku moze po prostu nie byc wydatku -
    dlatego progi i wyjatki na retencje siedza w app.etl.coverage, wspolne
    z dogrywka, ktora te dziury zasypuje.
    """
    from app.etl import coverage

    start, end, markets = coverage.survey(days)

    print(f"Pokrycie {start} .. {end} ({days} dni)")
    print()
    head = f"{'rynek':<7}" + "".join(f"{c.label:>13}" for c in markets[0].cells) if markets else "rynek"
    print(head)
    print("-" * len(head))

    for m in markets:
        print(f"{m.country:<7}" + "".join(f"{c.days:>10} dni" for c in m.cells))

    gaps = [(m, c) for m in markets for c in m.gaps]
    if gaps:
        print()
        print("Dziury do uzupelnienia:")
        for m, c in gaps:
            zakres = f" ({c.first} .. {c.last})" if c.first else " (pusto)"
            retencja = (
                f" [retencja Amazona {coverage.SEARCH_TERM_RETENTION} dni]"
                if c.kind == "search_term" else ""
            )
            print(f"  {m.country} / {c.label}: {c.days} z {c.expected} dni{zakres}{retencja}")
    else:
        print()
        print("Bez dziur - kazdy typ ma tyle dni, ile mozna dla niego pobrac.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="adspanel")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="sprawdza konfiguracje i polaczenie z API")
    sub.add_parser("initdb", help="tworzy schemat bazy")
    sub.add_parser("profiles", help="pobiera i zapisuje profile reklamowe")

    p_pass = sub.add_parser("setpassword", help="ustawia haslo dostepu do panelu")
    p_pass.add_argument("--password", help="jesli pominiesz, skrypt zapyta")

    p_sync = sub.add_parser("sync", help="pelny sync: encje + raporty")
    p_sync.add_argument("--profile", type=int, action="append", dest="profiles")
    p_sync.add_argument("--lookback", type=int, default=14)
    p_sync.add_argument("--no-reports", action="store_true")

    p_rep = sub.add_parser("reports", help="tylko raporty dla zakresu dat")
    p_rep.add_argument("--profile", type=int, required=True)
    p_rep.add_argument("--start")
    p_rep.add_argument("--end")
    p_rep.add_argument("--kinds", help="campaign,placement,targeting,search_term,product")

    p_sp = sub.add_parser(
        "spcheck", help="sprawdza dostep do SP-API i roli Brand Analytics"
    )

    p_sales = sub.add_parser(
        "sales", help="pobiera sprzedaz calkowita z SP-API (mianownik do TACOS)"
    )
    p_sales.add_argument("--days", type=int, default=95)
    p_sales.add_argument("--profile", type=int, default=None)

    p_ord = sub.add_parser(
        "orders", help="pobiera podzial sprzedazy na FBA i wysylke wlasna"
    )
    p_ord.add_argument("--days", type=int, default=30)

    p_cat = sub.add_parser(
        "catalog", help="pobiera tytuly i miniatury produktow (SP-API)"
    )
    p_cat.add_argument("--profile", type=int, default=None)
    p_cat.add_argument("--all", action="store_true", help="odswiez takze te, ktore juz mamy")
    p_cat.add_argument(
        "--szeroko", action="store_true",
        help="obejmij cala baze ofert oraz ASIN-y konkurencji z fraz i wykluczen",
    )

    p_list = sub.add_parser(
        "listings", help="pobiera cala baze produktowa z Seller Central (SP-API)"
    )
    p_list.add_argument("--profile", type=int, default=None)

    p_sb = sub.add_parser(
        "sb", help="pobiera encje Sponsored Brands (kampanie, grupy, keywordy)"
    )
    p_sb.add_argument("--profile", type=int, required=True)

    p_sd = sub.add_parser(
        "sd", help="pobiera encje Sponsored Display (kampanie, grupy, targety, reklamy)"
    )
    p_sd.add_argument("--profile", type=int, required=True)

    p_rap = sub.add_parser(
        "sbsd-reports", help="pobiera raporty Sponsored Brands / Display"
    )
    p_rap.add_argument("--profile", type=int, required=True)
    p_rap.add_argument("--type", choices=["SB", "SD"], required=True)
    p_rap.add_argument("--days", type=int, default=30)

    p_cov = sub.add_parser(
        "coverage", help="ile dni danych ma kazdy rynek w kazdym typie raportu"
    )
    p_cov.add_argument("--days", type=int, default=95)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    if args.command == "spcheck":
        from app.sp.client import check_access
        stan = check_access()
        print(json.dumps(stan, indent=2, ensure_ascii=False))
        return 0 if stan.get("sales_report") else 1

    if args.command == "sales":
        from app.etl import sales_sync
        end = dt.date.today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=args.days - 1)
        wynik = sales_sync.sync_all(start, end, args.profile)
        print(json.dumps(wynik, indent=2, ensure_ascii=False, default=str))
        return 0

    if args.command == "orders":
        # Jeden raport na cale konto - raport zamowien zwraca wszystkie rynki
        # naraz, wiec nie ma sensu pytac o kazdy osobno.
        from app.etl import orders_sync
        end = dt.date.today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=args.days - 1)
        wynik = orders_sync.sync_window(start, end)
        print(json.dumps(wynik, indent=2, ensure_ascii=False, default=str))
        return 0

    if args.command == "catalog":
        from app.etl import catalog
        wynik = catalog.sync_all(args.profile, only_stale=not args.all,
                                 szeroko=args.szeroko)
        print(json.dumps(wynik, indent=2, ensure_ascii=False))
        return 0

    if args.command == "listings":
        from app.etl import listings
        wynik = listings.sync_all(args.profile)
        print(json.dumps(wynik, indent=2, ensure_ascii=False, default=str))
        return 0

    if args.command == "sb":
        from app.etl import sb_sync
        wynik = sb_sync.sync_entities(args.profile)
        print(json.dumps(wynik, indent=2, ensure_ascii=False))
        return 0

    if args.command == "sd":
        from app.etl import sd_sync
        wynik = sd_sync.sync_entities(args.profile)
        print(json.dumps(wynik, indent=2, ensure_ascii=False))
        return 0

    if args.command == "sbsd-reports":
        from app.etl import sb_sd_reports
        end = dt.date.today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=args.days - 1)
        wynik = sb_sd_reports.sync_reports(args.profile, args.type, start, end)
        print(json.dumps(wynik, indent=2, ensure_ascii=False))
        return 0

    if args.command == "coverage":
        return _coverage(args.days)

    if args.command == "health":
        print(json.dumps({
            "region": settings.ads_region,
            "base_url": settings.ads_base_url,
            "database": settings.resolved_database_url(),
            "credentials_configured": settings.is_configured,
        }, indent=2))
        if not settings.is_configured:
            print("\nUzupelnij .env (ADS_CLIENT_ID / ADS_CLIENT_SECRET / ADS_REFRESH_TOKEN).")
            return 1
        from app.ads.auth import token_provider
        token = token_provider.get()
        print(f"LWA OK - access token dlugosci {len(token)}")
        from app.ads.client import list_profiles
        found = list_profiles()
        print(f"Widocznych profili: {len(found)}")
        for p in found:
            info = p.get("accountInfo") or {}
            print(f"  {p['profileId']}  {p.get('countryCode')}  "
                  f"{info.get('type')}  {info.get('name')}")
        return 0

    if args.command == "setpassword":
        import getpass

        from app.auth import generate_secret, hash_password

        password = args.password or getpass.getpass("Nowe haslo do panelu: ")
        if not args.password:
            if password != getpass.getpass("Powtorz haslo: "):
                print("Hasla sie roznia.")
                return 1
        if len(password) < 10:
            print("Haslo musi miec co najmniej 10 znakow.")
            return 1

        env_path = PROJECT_ROOT / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines()
        existing = {
            ln.split("=", 1)[0].strip(): ln.split("=", 1)[1].strip()
            for ln in lines if "=" in ln and not ln.lstrip().startswith("#")
        }

        updates = {"AUTH_PASSWORD_HASH": hash_password(password)}
        # Pusta linia `AUTH_SECRET=` (wzor konfiguracji) to tez brak sekretu -
        # bez niego auth_ready zostaje False i logowanie odrzuca kazde haslo.
        if not existing.get("AUTH_SECRET"):
            updates["AUTH_SECRET"] = generate_secret()

        for key, value in updates.items():
            for i, ln in enumerate(lines):
                if ln.strip().startswith(f"{key}="):
                    lines[i] = f"{key}={value}"
                    break
            else:
                lines.append(f"{key}={value}")

        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("Haslo zapisane jako skrot argon2 w .env")
        if "AUTH_SECRET" in updates:
            print("Wygenerowano tez AUTH_SECRET (klucz podpisujacy sesje)")
        print("\nZrestartuj backend, zeby zmiana zadzialala.")
        return 0

    if args.command == "initdb":
        init_db()
        print("Schemat bazy utworzony.")
        return 0

    init_db()

    if args.command == "profiles":
        rows = sync.sync_profiles()
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0

    if args.command == "sync":
        summary = sync.sync_all(
            profile_ids=args.profiles,
            lookback_days=args.lookback,
            with_reports=not args.no_reports,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return 0

    if args.command == "reports":
        kinds = [k.strip() for k in args.kinds.split(",")] if args.kinds else None
        result = sync.sync_reports(args.profile, _date(args.start), _date(args.end), kinds)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

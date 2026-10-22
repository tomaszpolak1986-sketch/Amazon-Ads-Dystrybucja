"""Sprawdza, ze API jest realnie zamkniete.

Testuje trzy rzeczy, na ktorych zalezy najbardziej:
  * bez zalogowania odczyt i zapis sa odrzucane,
  * zle haslo nie otwiera sesji,
  * po zalogowaniu wszystko dziala normalnie.

Szczegolna uwaga na trasy zapisu - to one moga zmienic konto reklamowe.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

READ_PATHS = ["/api/profiles", "/api/campaigns", "/api/rules", "/api/plans", "/api/changes"]
WRITE_PATHS = [
    ("/api/actions/bids", {"profile_id": 1, "changes": [{"id": "1", "kind": "KEYWORD", "bid": 1}]}),
    ("/api/plans/apply", {"plan_id": "cokolwiek"}),
    ("/api/actions/state", {"profile_id": 1, "entity_type": "CAMPAIGN", "ids": ["1"],
                            "state": "PAUSED"}),
    ("/api/sync/profiles", None),
]


def main() -> int:
    if settings.auth_disabled:
        print("AUTH_DISABLED=true - test nie ma sensu. Wylacz flage w .env.")
        return 1
    if not settings.auth_ready:
        print("Brak hasla. Ustaw: python backend/scripts/cli.py setpassword")
        return 1

    failures = 0
    client = TestClient(app)

    print("--- 1. Bez zalogowania ---")
    for path in READ_PATHS:
        code = client.get(path).status_code
        ok = code == 401
        failures += not ok
        print(f"  {'OK  ' if ok else 'FAIL'} GET  {path:<24} -> {code} (oczekiwane 401)")

    for path, body in WRITE_PATHS:
        code = client.post(path, json=body).status_code
        ok = code == 401
        failures += not ok
        print(f"  {'OK  ' if ok else 'FAIL'} POST {path:<24} -> {code} (oczekiwane 401)")

    print("\n--- 2. Health musi byc otwarty (Docker go odpytuje) ---")
    code = client.get("/api/health").status_code
    failures += code != 200
    print(f"  {'OK  ' if code == 200 else 'FAIL'} GET  /api/health           -> {code}")

    print("\n--- 3. Zle haslo ---")
    code = client.post("/api/auth/login", json={"password": "nie-to-haslo"}).status_code
    failures += code != 401
    print(f"  {'OK  ' if code == 401 else 'FAIL'} POST /api/auth/login       -> {code} (oczekiwane 401)")

    print("\n--- 4. Poprawne haslo ---")
    # Haslo podaje sie argumentem. Wpisane na stale wedrowaloby z kazda kopia
    # repozytorium - a to haslo do panelu, ktory ma prawo zapisu na koncie
    # reklamowym.
    if len(sys.argv) < 2:
        print("  POMINIETO - podaj haslo argumentem:")
        print("       python backend/scripts/test_auth.py <haslo>")
        return failures
    password = sys.argv[1]
    resp = client.post("/api/auth/login", json={"password": password})
    ok = resp.status_code == 200
    failures += not ok
    print(f"  {'OK  ' if ok else 'FAIL'} POST /api/auth/login       -> {resp.status_code}")
    if not ok:
        print("       ", resp.text[:200])
        print("\nPodaj haslo argumentem: python backend/scripts/test_auth.py <haslo>")
        return 1

    print("\n--- 5. Po zalogowaniu ---")
    for path in READ_PATHS:
        code = client.get(path).status_code
        ok = code == 200
        failures += not ok
        print(f"  {'OK  ' if ok else 'FAIL'} GET  {path:<24} -> {code}")

    print("\n--- 6. Wylogowanie zamyka dostep ---")
    client.post("/api/auth/logout")
    code = client.get("/api/profiles").status_code
    failures += code != 401
    print(f"  {'OK  ' if code == 401 else 'FAIL'} GET  /api/profiles         -> {code} (oczekiwane 401)")

    print(f"\n{'WSZYSTKO OK' if not failures else f'BLEDOW: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

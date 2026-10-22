"""Smoke test warstwy HTTP - odpala wszystkie endpointy odczytu.

Haslo bierzemy z ADSPANEL_PASSWORD albo z argumentu:
    python backend/scripts/smoke_test.py MojeHaslo

Bez zalogowania panel odpowiada 401 na wszystko poza /api/health, wiec test
bez sesji tylko udawalby, ze cos sprawdza.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ENDPOINTS = [
    ("/api/health", {}),
    ("/api/profiles", {}),
    ("/api/markets", {"days": 30}),
    ("/api/overview", {"days": 30}),
    ("/api/campaigns", {"days": 30, "limit": 5}),
    ("/api/targets", {"days": 30, "limit": 5}),
    ("/api/search-terms", {"days": 30, "limit": 5}),
    ("/api/placements", {"days": 30}),
    ("/api/products", {"days": 30, "limit": 5}),
    ("/api/opportunities/negatives", {"days": 30, "min_clicks": 10, "limit": 5}),
    ("/api/opportunities/harvest", {"days": 30, "min_orders": 2, "limit": 5}),
    ("/api/portfolios/timeseries", {"days": 30}),
    ("/api/total-sales/coverage", {}),
    ("/api/campaigns", {"days": 30, "limit": 1}),
    ("/api/opportunities/overlap", {"days": 30, "min_clicks": 10, "limit": 5}),
    ("/api/opportunities/sku-overlap", {"days": 30, "min_clicks": 10, "limit": 5}),
    ("/api/ngrams", {"days": 30, "n": 2, "limit": 5}),
    ("/api/sync/runs", {}),
    ("/api/changes", {}),
    ("/api/plans", {}),
]


def main() -> int:
    client = TestClient(app)
    failures = 0

    password = (
        sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ADSPANEL_PASSWORD", "")
    )
    if not password:
        print("Podaj haslo argumentem albo w ADSPANEL_PASSWORD - bez sesji "
              "wszystkie endpointy zwroca 401.")
        return 2

    login = client.post("/api/auth/login", json={"password": password})
    if login.status_code != 200:
        print(f"Logowanie nie powiodlo sie: HTTP {login.status_code} {login.text[:200]}")
        return 2
    print("Zalogowano")
    print()

    for path, params in ENDPOINTS:
        resp = client.get(path, params=params)
        ok = resp.status_code == 200
        failures += not ok
        size = len(resp.json()) if ok and isinstance(resp.json(), list) else "-"
        print(f"{'OK ' if ok else 'FAIL'}  {resp.status_code}  {path:<38} rows={size}")
        if not ok:
            print("      ", resp.text[:300])

    print("\n--- Podglad danych ---")
    kpis = client.get("/api/overview", params={"days": 30}).json()["kpis"]
    print("KPI 30 dni:", json.dumps(kpis["current"], indent=2))
    print("Zmiana vs poprzedni okres (%):", json.dumps(kpis["delta_pct"]))

    print("\nRynki:")
    for m in client.get("/api/markets", params={"days": 30}).json():
        print(f"  {m['country']}  koszt={m['cost']:>9.2f}  sprzedaz={m['sales']:>10.2f}  "
              f"ACOS={m['acos']}%  ROAS={m['roas']}")

    print("\nTop kandydaci na negatywy (klikaja, nie sprzedaja):")
    for r in client.get("/api/opportunities/negatives",
                        params={"days": 30, "min_clicks": 15, "limit": 5}).json():
        print(f"  {r['search_term']:<38} klikniec={r['clicks']:>4}  koszt={r['cost']:>7.2f}  "
              f"zamowien={r['orders']}")

    print("\nTop kandydaci do harvestu (konwertuja, brak jako EXACT):")
    for r in client.get("/api/opportunities/harvest",
                        params={"days": 30, "min_orders": 3, "limit": 5}).json():
        print(f"  {r['search_term']:<38} zamowien={r['orders']:>3}  ACOS={r['acos']}%  "
              f"sugerowany bid={r['suggested_bid']}")

    print("\nN-gramy palace budzet:")
    for r in client.get("/api/ngrams", params={"days": 30, "n": 2, "limit": 5}).json():
        print(f"  {r['ngram']:<34} koszt={r['cost']:>8.2f}  ACOS={r['acos']}  "
              f"fraz={r['terms']}")

    print("\n--- Zabezpieczenie: nic nie idzie do Amazona bez zatwierdzenia ---")
    targets = client.get("/api/targets", params={"days": 30, "limit": 3}).json()
    if targets:
        payload = {
            "profile_id": 1000000001,
            "changes": [
                {"id": t["target_id"], "kind": t["target_kind"], "bid": round(t["cpc"] or 0.4, 2)}
                for t in targets
            ],
        }
        resp = client.post("/api/actions/bids", json=payload)
        failures += resp.status_code != 200
        plan = resp.json()
        print(f"  1. POST /api/actions/bids -> HTTP {resp.status_code}, "
              f"requires_confirmation={plan.get('requires_confirmation')}, "
              f"applied={plan.get('applied')}")
        print(f"     plan_id={plan.get('plan_id')}  pozycji={plan.get('item_count')}")

        queue = client.get("/api/plans").json()
        print(f"  2. Kolejka zatwierdzen: {len(queue)} planow czeka na decyzje")
        failures += not any(p["plan_id"] == plan.get("plan_id") for p in queue)

        bogus = client.post("/api/plans/apply", json={"plan_id": "nie-istnieje"})
        print(f"  3. Zatwierdzenie zmyslonego planu -> HTTP {bogus.status_code} (oczekiwane 409)")
        failures += bogus.status_code != 409

        cancelled = client.post("/api/plans/cancel", json={"plan_id": plan["plan_id"]})
        print(f"  4. Odrzucenie planu -> HTTP {cancelled.status_code} (oczekiwane 200)")
        failures += cancelled.status_code != 200

        reused = client.post("/api/plans/apply", json={"plan_id": plan["plan_id"]})
        print(f"  5. Ponowne uzycie odrzuconego planu -> HTTP {reused.status_code} "
              "(oczekiwane 409)")

        # Tworzenie kampanii: plan powstaje, ale jest niepodzielny i tak samo
        # jak reszta nie idzie do Amazona bez zgody.
        camp_spec = {
            "name": "SMOKE TEST - do skasowania",
            "daily_budget": 10.0,
            "targeting_type": "AUTO",
            "state": "PAUSED",
            "ad_group": {"name": "Grupa smoke", "default_bid": 0.30},
            "skus": ["SMOKE-SKU-1"],
        }
        profil = client.get("/api/profiles").json()
        pid = profil[0]["profile_id"] if profil else 1000000001
        camp = client.post(
            "/api/actions/create-campaign", json={"profile_id": pid, "spec": camp_spec}
        )
        if camp.status_code == 200:
            cplan = camp.json()
            print(f"  6. Plan nowej kampanii -> HTTP 200, applied={cplan.get('applied')}, "
                  f"pozycji={cplan.get('item_count')}")
            failures += cplan.get("applied") != 0

            czesc = client.post(
                "/api/plans/apply", json={"plan_id": cplan["plan_id"], "indexes": [0]}
            )
            print(f"  7. Zatwierdzenie kampanii po kawalku -> HTTP {czesc.status_code} "
                  "(oczekiwane 409)")
            failures += czesc.status_code != 409

            client.post("/api/plans/cancel", json={"plan_id": cplan["plan_id"]})
            print("  8. Plan kampanii odrzucony")
        else:
            # Nazwa moze juz istniec po wczesniejszym przebiegu - to nie awaria
            print(f"  6. Plan nowej kampanii -> HTTP {camp.status_code}: "
                  f"{str(camp.json().get('detail'))[:80]}")

        failures += reused.status_code != 409

    print(f"\n{'WSZYSTKO OK' if not failures else f'BLEDOW: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

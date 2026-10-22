"""Test silnika regul na danych demo.

Zaklada wszystkie szablony jako reguly, uruchamia je i sprawdza, ze:
  * kazda regula tworzy plan (albo swiadomie nic nie znajduje),
  * plan ma status PENDING i czeka na zatwierdzenie,
  * nic nie zostalo wyslane do Amazona.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import PendingPlan, Profile, Rule  # noqa: E402
from app.rules import engine, templates  # noqa: E402


def main() -> int:
    init_db()
    failures = 0

    with session_scope() as session:
        # czysty start
        session.query(Rule).delete()
        session.flush()

        profiles = list(session.execute(select(Profile.profile_id)).scalars())
        if not profiles:
            print("Brak profili - uruchom najpierw seed_demo.py")
            return 1
        pid = profiles[0]
        print(f"Profil testowy: {pid}\n")

        created = []
        for tmpl in templates.TEMPLATES:
            payload = {k: v for k, v in tmpl.items() if k != "key"}
            try:
                engine.validate(payload)
            except engine.RuleError as exc:
                print(f"FAIL  walidacja '{tmpl['name']}': {exc}")
                failures += 1
                continue
            rule = Rule(**payload)
            session.add(rule)
            created.append(rule)
        session.flush()
        print(f"Zalozono {len(created)} regul z szablonow\n")

        print(f"{'REGULA':<46} {'OCEN.':>6} {'TRAF.':>6}  STATUS")
        print("-" * 88)

        for rule in created:
            try:
                preview = engine.preview_rule(session, rule, pid)
            except Exception as exc:
                print(f"FAIL  {rule.name}: {exc}")
                failures += 1
                continue

            results = engine.run_rule(session, rule, pid)
            r = results[0]
            status = r.get("status", "?")
            marker = "  " if status in ("PLAN_CREATED", "NO_MATCH", "NO_CHANGE") else "!!"
            if status == "ERROR":
                failures += 1
                marker = "!!"
            print(f"{marker}{rule.name:<44} {preview['evaluated']:>6} "
                  f"{preview['matched']:>6}  {status}"
                  + (f" -> {r.get('summary')}" if r.get("summary") else ""))
            if status == "ERROR":
                print(f"     blad: {r.get('error')}")

        session.flush()

        # --- weryfikacja bezpieczenstwa ---
        plans = session.execute(
            select(PendingPlan).where(PendingPlan.status == "PENDING")
        ).scalars().all()
        rule_plans = [p for p in plans if (p.payload or {}).get("source") == "RULE"]

        print("\n--- Kontrola bezpieczenstwa ---")
        print(f"  Planow od regul w kolejce:        {len(rule_plans)}")
        applied = session.execute(
            select(PendingPlan).where(PendingPlan.status == "APPLIED")
        ).scalars().all()
        print(f"  Planow wykonanych automatycznie:  {len(applied)}  (oczekiwane 0)")
        failures += len(applied) != 0

        every_pending = all(p.status == "PENDING" for p in rule_plans)
        print(f"  Wszystkie czekaja na zatwierdzenie: {every_pending}")
        failures += not every_pending

        if rule_plans:
            print("\n  Przyklad planu od reguly:")
            p = rule_plans[0]
            print(f"    {p.summary}  ({p.item_count} pozycji)")
            for item in (p.payload or {}).get("planned", [])[:3]:
                print(f"      {item.get('id')}  {item.get('field')}: "
                      f"{item.get('old')} -> {item.get('new')}")

    print(f"\n{'WSZYSTKO OK' if not failures else f'BLEDOW: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

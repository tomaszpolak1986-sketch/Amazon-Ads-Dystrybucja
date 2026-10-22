"""Silnik regul.

Regula = zakres danych + warunki + akcja. Uruchomienie reguly:

    1. pobiera zagregowane dane z tego samego zrodla, co dashboard,
    2. odfiltrowuje wiersze spelniajace warunki,
    3. tworzy PendingPlan ze zrodlem RULE.

Regula NIGDY nie wykonuje zmiany. Wynik zawsze laduje w kolejce zatwierdzen -
tak samo jak akcja wykonana recznie. To swiadome ograniczenie, nie brak funkcji:
uzytkownik chce widziec i zaakceptowac kazda zmiane przed wyslaniem.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api import actions, queries
from ..config import settings
from ..models import AdGroup, Campaign, Keyword, Profile, Rule, RuleRun, Target
from ..timeutil import utcnow

log = logging.getLogger(__name__)

DEFAULT_MAX_ITEMS = 200


# --------------------------------------------------------------------------- #
# Zakresy i metryki
# --------------------------------------------------------------------------- #

SCOPES = {
    "TARGET": "Keywordy i targety produktowe",
    "SEARCH_TERM": "Frazy wyszukiwania",
    "CAMPAIGN": "Kampanie",
    "PLACEMENT": "Placementy w kampaniach",
    "PRODUCT": "Produkty (SKU/ASIN)",
}

# Metryki dostepne w warunkach - te same, ktore widac w tabelach panelu
METRICS = [
    "impressions", "clicks", "cost", "orders", "sales", "units",
    "ctr", "cpc", "cvr", "acos", "roas", "cpa", "bid",
    # Udzial wyswietlen na gorze wynikow. Amazon podaje go dla kampanii
    # i targetow, przy placementach nigdy - regula na placementach z tym
    # warunkiem nigdy sie nie odpali i to jest poprawne zachowanie.
    "tos_share",
]

# Akcje dopuszczalne w danym zakresie
SCOPE_ACTIONS: dict[str, list[str]] = {
    "TARGET": ["SET_BID_PCT", "SET_BID_TARGET_ACOS", "PAUSE", "ENABLE"],
    "SEARCH_TERM": ["ADD_NEGATIVE_KEYWORD", "ADD_NEGATIVE_ASIN", "HARVEST_KEYWORD"],
    "CAMPAIGN": ["SET_BUDGET_PCT", "PAUSE", "ENABLE"],
    "PLACEMENT": ["SET_PLACEMENT_MODIFIER"],
    "PRODUCT": [],  # na razie tylko raportowo
}

OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


class RuleError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Ocena warunkow
# --------------------------------------------------------------------------- #


def _value(row: dict[str, Any], metric: str) -> float | None:
    """Wartosc metryki. None oznacza 'nie da sie policzyc' (np. ACOS bez sprzedazy)."""
    raw = row.get(metric)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _check(row: dict[str, Any], cond: dict[str, Any]) -> bool:
    op = OPS.get(cond.get("op", "=="))
    if op is None:
        raise RuleError(f"Nieznany operator: {cond.get('op')}")

    value = _value(row, cond["metric"])
    if value is None:
        # ACOS/ROAS/CPA sa None gdy nie bylo sprzedazy. Traktujemy to jako
        # "nieskonczenie zle" dla progow gornych i jako niespelnione dla dolnych -
        # inaczej regula "ACOS > 50%" pomijalaby wlasnie te najgorsze pozycje.
        if cond["metric"] in ("acos", "cpa") and cond.get("op") in (">", ">="):
            return (row.get("clicks") or 0) > 0
        if cond["metric"] == "roas" and cond.get("op") in ("<", "<="):
            return (row.get("clicks") or 0) > 0
        return False

    return op(value, float(cond["value"]))


def evaluate_conditions(row: dict[str, Any], conditions: dict[str, Any]) -> bool:
    all_conds = conditions.get("all") or []
    any_conds = conditions.get("any") or []

    if all_conds and not all(_check(row, c) for c in all_conds):
        return False
    if any_conds and not any(_check(row, c) for c in any_conds):
        return False
    return bool(all_conds or any_conds)


def _passes_filters(row: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True

    name = (row.get("campaign_name") or row.get("name") or "").lower()
    if (needle := filters.get("campaign_name_contains")):
        if needle.lower() not in name:
            return False
    if (needle := filters.get("campaign_name_excludes")):
        if needle.lower() in name:
            return False
    if (allowed := filters.get("match_type")):
        if (row.get("match_type") or "") not in allowed:
            return False
    if (allowed := filters.get("state")):
        if (row.get("state") or "") not in allowed:
            return False
    if (allowed := filters.get("target_kind")):
        if (row.get("target_kind") or "") not in allowed:
            return False
    return True


# --------------------------------------------------------------------------- #
# Pobranie danych dla zakresu
# --------------------------------------------------------------------------- #


def _fetch(session: Session, rule: Rule, profile_id: int, start: dt.date, end: dt.date):
    scope = rule.scope.upper()
    if scope == "TARGET":
        return queries.targets(session, profile_id, start, end, limit=10000)
    if scope == "SEARCH_TERM":
        return queries.search_terms(session, profile_id, start, end, limit=10000)
    if scope == "CAMPAIGN":
        return queries.campaigns(session, profile_id, start, end, limit=5000)
    if scope == "PRODUCT":
        return queries.products(session, profile_id, start, end, limit=5000)
    if scope == "PLACEMENT":
        return _fetch_placements(session, profile_id, start, end)
    raise RuleError(f"Nieznany zakres: {rule.scope}")


def _fetch_placements(session: Session, profile_id: int, start: dt.date, end: dt.date):
    """Placementy per kampania - kazdy wiersz to jeden placement w jednej kampanii."""
    from sqlalchemy import func

    from ..models import PlacementDaily

    stmt = (
        select(
            PlacementDaily.campaign_id,
            PlacementDaily.placement,
            Campaign.name.label("campaign_name"),
            Campaign.state.label("state"),
            Campaign.bidding_strategy.label("strategy"),
            Campaign.placement_bidding.label("placement_bidding"),
            func.sum(PlacementDaily.impressions).label("impressions"),
            func.sum(PlacementDaily.clicks).label("clicks"),
            func.sum(PlacementDaily.cost).label("cost"),
            func.sum(PlacementDaily.orders).label("orders"),
            func.sum(PlacementDaily.sales).label("sales"),
            func.sum(PlacementDaily.units).label("units"),
        )
        .join(Campaign, Campaign.campaign_id == PlacementDaily.campaign_id, isouter=True)
        .where(queries._window(PlacementDaily, profile_id, start, end))
        .group_by(
            PlacementDaily.campaign_id, PlacementDaily.placement, Campaign.name,
            Campaign.state, Campaign.bidding_strategy, Campaign.placement_bidding,
        )
    )
    return [queries._derive(dict(r)) for r in session.execute(stmt).mappings().all()]


# --------------------------------------------------------------------------- #
# Budowa planu z dopasowanych wierszy
# --------------------------------------------------------------------------- #


def _build_plan(
    session: Session, rule: Rule, profile_id: int, matched: list[dict[str, Any]]
) -> dict[str, Any] | None:
    action = rule.action or {}
    kind = (action.get("type") or "").upper()
    limits = rule.limits or {}
    max_items = int(limits.get("max_items") or DEFAULT_MAX_ITEMS)
    matched = matched[:max_items]

    if not matched:
        return None

    if kind == "SET_BID_PCT":
        percent = float(action.get("percent", 0))
        min_bid = float(limits.get("min_bid", 0.02))
        max_bid = float(limits.get("max_bid", 100.0))
        changes = []
        for row in matched:
            base = row.get("bid") or row.get("cpc") or 0
            if not base:
                continue
            new_bid = max(min_bid, min(max_bid, round(base * (1 + percent / 100), 2)))
            changes.append({"id": row["target_id"], "kind": row["target_kind"], "bid": new_bid})
        if not changes:
            return None
        return actions.plan_bids(session, profile_id, changes, source="RULE", rule_id=rule.id)

    if kind == "SET_BID_TARGET_ACOS":
        target_acos = float(action.get("target_acos", 30))
        min_bid = float(limits.get("min_bid", 0.02))
        max_bid = float(limits.get("max_bid", 100.0))
        max_change = float(limits.get("max_change_pct", 50))
        changes = []
        for row in matched:
            clicks = row.get("clicks") or 0
            base = row.get("bid") or row.get("cpc") or 0
            if not clicks or not base:
                continue
            # stawka docelowa = przychod na klikniecie * docelowy ACOS
            rpc = (row.get("sales") or 0) / clicks
            raw = rpc * (target_acos / 100)
            # ogranicz skok wzgledem obecnej stawki, zeby jedna dobra konwersja
            # nie wywindowala bidu kilkukrotnie
            low = base * (1 - max_change / 100)
            high = base * (1 + max_change / 100)
            new_bid = max(min_bid, min(max_bid, round(max(low, min(high, raw)), 2)))
            changes.append({"id": row["target_id"], "kind": row["target_kind"], "bid": new_bid})
        if not changes:
            return None
        return actions.plan_bids(session, profile_id, changes, source="RULE", rule_id=rule.id)

    if kind in ("PAUSE", "ENABLE"):
        state = "PAUSED" if kind == "PAUSE" else "ENABLED"
        scope = rule.scope.upper()
        if scope == "CAMPAIGN":
            ids = [r["campaign_id"] for r in matched]
            return actions.plan_state(
                session, profile_id, "CAMPAIGN", ids, state, source="RULE", rule_id=rule.id
            )
        # keywordy i targety to osobne zasoby - plan obejmuje jeden typ
        keywords = [r["target_id"] for r in matched if r.get("target_kind") == "KEYWORD"]
        targets = [r["target_id"] for r in matched if r.get("target_kind") == "TARGET"]
        entity_type, ids = ("KEYWORD", keywords) if keywords else ("TARGET", targets)
        if not ids:
            return None
        if keywords and targets:
            log.info(
                "Regula %s: dopasowala keywordy i targety; plan obejmuje %s "
                "(pozostale trafia do kolejnego uruchomienia)",
                rule.id, entity_type,
            )
        return actions.plan_state(
            session, profile_id, entity_type, ids, state, source="RULE", rule_id=rule.id
        )

    if kind == "SET_BUDGET_PCT":
        percent = float(action.get("percent", 0))
        min_budget = float(limits.get("min_budget", 1.0))
        max_budget = float(limits.get("max_budget", 10000.0))
        changes = []
        for row in matched:
            base = row.get("budget") or 0
            if not base:
                continue
            new_budget = max(min_budget, min(max_budget, round(base * (1 + percent / 100), 2)))
            changes.append({"campaign_id": row["campaign_id"], "budget": new_budget})
        if not changes:
            return None
        return actions.plan_budgets(session, profile_id, changes, source="RULE", rule_id=rule.id)

    if kind == "ADD_NEGATIVE_KEYWORD":
        match_type = action.get("match_type", "NEGATIVE_EXACT")
        existing = _existing_negatives(session, profile_id)
        items = []
        for row in matched:
            term = (row.get("search_term") or "").strip()
            ag = row.get("ad_group_id")
            if not term or not ag or (ag, term.lower()) in existing:
                continue
            items.append({
                "campaignId": row["campaign_id"],
                "adGroupId": ag,
                "keywordText": term,
                "matchType": match_type,
            })
        if not items:
            return None
        return actions.plan_negatives(
            session, profile_id, items, source="RULE", rule_id=rule.id
        )

    if kind == "ADD_NEGATIVE_ASIN":
        items = []
        for row in matched:
            term = (row.get("search_term") or "").strip().upper()
            ag = row.get("ad_group_id")
            # w raporcie search term dla targetowania produktowego fraza to ASIN
            if not ag or not _looks_like_asin(term):
                continue
            items.append({"campaignId": row["campaign_id"], "adGroupId": ag, "asin": term})
        if not items:
            return None
        return actions.plan_negative_targets(
            session, profile_id, items, source="RULE", rule_id=rule.id
        )

    if kind == "HARVEST_KEYWORD":
        match_type = action.get("match_type", "EXACT")
        target_acos = float(action.get("target_acos", 30))
        existing = _existing_keywords(session, profile_id, match_type)
        items = []
        for row in matched:
            term = (row.get("search_term") or "").strip()
            ag = row.get("ad_group_id")
            if not term or not ag or term.lower() in existing:
                continue
            clicks = row.get("clicks") or 0
            bid = None
            if clicks:
                rpc = (row.get("sales") or 0) / clicks
                cpc = row.get("cpc") or 0
                bid = round(max(0.02, min(rpc * (target_acos / 100), cpc * 3 or 99)), 2)
            items.append({
                "campaignId": row["campaign_id"],
                "adGroupId": ag,
                "keywordText": term,
                "matchType": match_type,
                **({"bid": bid} if bid else {}),
            })
        if not items:
            return None
        return actions.plan_harvest(session, profile_id, items, source="RULE", rule_id=rule.id)

    if kind == "SET_PLACEMENT_MODIFIER":
        return _plan_placement_shift(session, rule, profile_id, matched, action, limits)

    raise RuleError(f"Nieznana akcja: {kind}")


def _plan_placement_shift(
    session: Session,
    rule: Rule,
    profile_id: int,
    matched: list[dict[str, Any]],
    action: dict[str, Any],
    limits: dict[str, Any],
) -> dict[str, Any] | None:
    """Podnosi modyfikator placementow, ktore spelnily warunki.

    Amazon nadpisuje caly zestaw placementBidding, wiec dla kazdej dotknietej
    kampanii odtwarzamy komplet placementow: dopasowane dostaja nowy procent,
    reszta zachowuje obecny.
    """
    step = float(action.get("step", 10))
    max_percent = float(action.get("max_percent", 100))
    min_percent = float(limits.get("min_percent", 0))

    by_campaign: dict[str, dict[str, Any]] = {}
    for row in matched:
        cid = row["campaign_id"]
        entry = by_campaign.setdefault(
            cid,
            {
                "campaign_id": cid,
                "strategy": row.get("strategy") or "LEGACY_FOR_SALES",
                "current": {
                    p.get("placement"): float(p.get("percentage") or 0)
                    for p in (row.get("placement_bidding") or [])
                },
                "hits": set(),
            },
        )
        entry["hits"].add(row["placement"])

    changes = []
    for entry in by_campaign.values():
        current = entry["current"]
        placements = []
        for placement in ("PLACEMENT_TOP", "PLACEMENT_REST_OF_SEARCH", "PLACEMENT_PRODUCT_PAGE"):
            old = current.get(placement, 0.0)
            new = old
            if placement in entry["hits"]:
                new = max(min_percent, min(max_percent, old + step))
            placements.append({"placement": placement, "old": old, "new": new})
        if any(round(p["old"]) != round(p["new"]) for p in placements):
            changes.append({
                "campaign_id": entry["campaign_id"],
                "strategy": entry["strategy"],
                "placements": placements,
            })

    if not changes:
        return None
    return actions.plan_placements(session, profile_id, changes, source="RULE", rule_id=rule.id)


# --------------------------------------------------------------------------- #
# Deduplikacja wzgledem stanu konta
# --------------------------------------------------------------------------- #


def _existing_negatives(session: Session, profile_id: int) -> set[tuple[str, str]]:
    from ..models import NegativeKeyword

    rows = session.execute(
        select(NegativeKeyword.ad_group_id, NegativeKeyword.keyword_text).where(
            NegativeKeyword.profile_id == profile_id
        )
    ).all()
    return {(ag, (text or "").lower()) for ag, text in rows if ag}


def _existing_keywords(session: Session, profile_id: int, match_type: str) -> set[str]:
    rows = session.execute(
        select(Keyword.keyword_text).where(
            Keyword.profile_id == profile_id, Keyword.match_type == match_type
        )
    ).scalars()
    return {(t or "").strip().lower() for t in rows}


def _looks_like_asin(text: str) -> bool:
    return len(text) == 10 and text.startswith("B") and text.isalnum()


# --------------------------------------------------------------------------- #
# Uruchomienie reguly
# --------------------------------------------------------------------------- #


def preview_rule(session: Session, rule: Rule, profile_id: int) -> dict[str, Any]:
    """Ocenia regule bez tworzenia planu - do podgladu przy edycji."""
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=rule.lookback_days - 1)

    rows = _fetch(session, rule, profile_id, start, end)
    matched = [
        r for r in rows
        if _passes_filters(r, rule.filters) and evaluate_conditions(r, rule.conditions)
    ]
    limit = int((rule.limits or {}).get("max_items") or DEFAULT_MAX_ITEMS)

    return {
        "evaluated": len(rows),
        "matched": len(matched),
        "limited_to": min(len(matched), limit),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "sample": matched[:25],
    }


def run_rule(
    session: Session,
    rule: Rule,
    profile_id: int | None = None,
    scheduled: bool = False,
) -> list[dict[str, Any]]:
    """Uruchamia regule dla jednego lub wszystkich rynkow.

    Zwraca liste wynikow per profil. Kazde dopasowanie tworzy PendingPlan
    czekajacy na zatwierdzenie - nic nie jest wysylane do Amazona.

    Przy `scheduled=True` plan powstaje tylko wtedy, gdy laczny wydatek
    dopasowanych pozycji siega progu (`auto_queue_min_cost`). Bez tego kolejka
    zatwierdzen zapelnialaby sie w tle drobiazgami, a wtedy przestaje pelnic
    swoja role: nikt nie przeglada listy, na ktorej wszystko wyglada tak samo.
    Uruchomienie reczne omija prog, bo user sam o nie poprosil.
    """
    targets = (
        [profile_id]
        if profile_id
        else [rule.profile_id]
        if rule.profile_id
        else list(session.execute(select(Profile.profile_id)).scalars())
    )

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=rule.lookback_days - 1)
    results = []

    for pid in targets:
        run = RuleRun(rule_id=rule.id, profile_id=pid)
        session.add(run)
        try:
            rows = _fetch(session, rule, pid, start, end)
            matched = [
                r for r in rows
                if _passes_filters(r, rule.filters) and evaluate_conditions(r, rule.conditions)
            ]
            run.evaluated = len(rows)
            run.matched = len(matched)

            stawka = sum(r.get("cost") or 0 for r in matched)
            prog = float((rule.limits or {}).get("min_cost", settings.auto_queue_min_cost))
            ponizej_progu = scheduled and stawka < prog

            if ponizej_progu:
                run.status = "BELOW_THRESHOLD"
                run.detail = (
                    f"Dopasowano {len(matched)} pozycji o lacznym wydatku "
                    f"{stawka:.2f}, ponizej progu {prog:.2f} dla uruchomien "
                    "automatycznych - planu nie utworzono"
                )
                results.append({
                    "profile_id": pid,
                    "evaluated": run.evaluated,
                    "matched": run.matched,
                    "cost_at_stake": round(stawka, 2),
                    "status": run.status,
                    "plan_id": None,
                    "summary": run.detail,
                })
                continue

            plan = _build_plan(session, rule, pid, matched) if matched else None
            if plan:
                run.plan_id = plan["plan_id"]
                run.status = "PLAN_CREATED"
                run.detail = plan["summary"]
            else:
                run.status = "NO_MATCH" if not matched else "NO_CHANGE"
                run.detail = (
                    "Brak dopasowan" if not matched
                    else "Dopasowania nie prowadza do zmiany wartosci"
                )

            results.append({
                "profile_id": pid,
                "evaluated": run.evaluated,
                "matched": run.matched,
                "cost_at_stake": round(stawka, 2),
                "status": run.status,
                "plan_id": run.plan_id,
                "summary": run.detail,
            })
        except Exception as exc:  # jedna regula nie moze wywrocic calego przebiegu
            log.exception("Regula %s (profil %s) nie powiodla sie", rule.id, pid)
            run.status = "ERROR"
            run.detail = str(exc)[:500]
            results.append({"profile_id": pid, "status": "ERROR", "error": str(exc)[:300]})

    rule.last_run_at = utcnow()
    return results


def run_due_rules(session: Session) -> dict[str, Any]:
    """Uruchamia reguly, ktorych minal interwal. Wolane przez scheduler."""
    now = utcnow()
    rules = session.execute(
        select(Rule).where(Rule.enabled.is_(True), Rule.schedule_hours.isnot(None))
    ).scalars().all()

    summary = []
    for rule in rules:
        due = (
            rule.last_run_at is None
            or rule.last_run_at + dt.timedelta(hours=rule.schedule_hours) <= now
        )
        if not due:
            continue
        log.info("Scheduler: uruchamiam regule %s (%s)", rule.id, rule.name)
        summary.append({
            "rule_id": rule.id,
            "name": rule.name,
            "results": run_rule(session, rule, scheduled=True),
        })

    return {"ran": len(summary), "details": summary}


def describe(rule: Rule) -> str:
    """Czytelny opis reguly - do listy w panelu i do logow."""
    conds = rule.conditions or {}
    parts = []
    for key, joiner in (("all", " i "), ("any", " lub ")):
        group = conds.get(key) or []
        if group:
            parts.append(joiner.join(f"{c['metric']} {c['op']} {c['value']}" for c in group))
    action = rule.action or {}
    return f"{SCOPES.get(rule.scope, rule.scope)}: {' oraz '.join(parts)} -> {action.get('type')}"


def validate(payload: dict[str, Any]) -> None:
    """Sprawdza spojnosc definicji reguly przed zapisem."""
    scope = (payload.get("scope") or "").upper()
    if scope not in SCOPES:
        raise RuleError(f"Nieznany zakres: {scope}. Dozwolone: {', '.join(SCOPES)}")

    action_type = ((payload.get("action") or {}).get("type") or "").upper()
    allowed = SCOPE_ACTIONS.get(scope, [])
    if action_type not in allowed:
        raise RuleError(
            f"Akcja {action_type} nie pasuje do zakresu {scope}. "
            f"Dozwolone: {', '.join(allowed) or 'brak'}"
        )

    conditions = payload.get("conditions") or {}
    groups = (conditions.get("all") or []) + (conditions.get("any") or [])
    if not groups:
        raise RuleError("Regula musi mieć przynajmniej jeden warunek")

    for cond in groups:
        if cond.get("metric") not in METRICS:
            raise RuleError(
                f"Nieznana metryka: {cond.get('metric')}. Dozwolone: {', '.join(METRICS)}"
            )
        if cond.get("op") not in OPS:
            raise RuleError(f"Nieznany operator: {cond.get('op')}")
        try:
            float(cond.get("value"))
        except (TypeError, ValueError):
            raise RuleError(f"Wartość warunku musi być liczbą: {cond.get('value')}") from None


def to_dict(rule: Rule, last_run: RuleRun | None = None) -> dict[str, Any]:
    from ..timeutil import iso_utc

    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "enabled": rule.enabled,
        "profile_id": rule.profile_id,
        "scope": rule.scope,
        "lookback_days": rule.lookback_days,
        "filters": rule.filters,
        "conditions": rule.conditions,
        "action": rule.action,
        "limits": rule.limits,
        "schedule_hours": rule.schedule_hours,
        "last_run_at": iso_utc(rule.last_run_at),
        "summary": describe(rule),
        "last_run": (
            {
                "ts": iso_utc(last_run.ts),
                "evaluated": last_run.evaluated,
                "matched": last_run.matched,
                "status": last_run.status,
                "plan_id": last_run.plan_id,
                "detail": last_run.detail,
            }
            if last_run
            else None
        ),
    }

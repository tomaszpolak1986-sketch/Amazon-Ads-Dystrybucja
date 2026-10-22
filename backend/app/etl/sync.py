"""ETL: pobranie stanu konta i raportow z Ads API do lokalnej bazy.

Strategia idempotencji:
  * encje (kampanie, keywordy, targety) - upsert po kluczu glownym,
  * fakty dzienne - kasujemy zakres dat dla profilu i wstawiamy na nowo.
    Dzieki temu ponowny sync tego samego okna nie duplikuje wierszy, a jednoczesnie
    zaciaga dojrzala atrybucje (Amazon domyka konwersje jeszcze kilka dni po klikniecie).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from sqlalchemy import and_, delete, select, update
from sqlalchemy.orm import Session

from ..ads import entities, reports
from ..api import targeting_names
from ..ads.client import AdsClient, list_profiles
from ..db import session_scope
from ..timeutil import utcnow
from .zapis import wstaw_lub_zastap
from ..models import (
    AdGroup,
    Campaign,
    CampaignDaily,
    Keyword,
    NegativeKeyword,
    NegativeTarget,
    PlacementDaily,
    Portfolio,
    ProductAd,
    ProductDaily,
    Profile,
    SearchTermDaily,
    SyncRun,
    Target,
    TargetDaily,
)

log = logging.getLogger(__name__)

# Ile ostatnich dni odswiezac przy rutynowym syncu (atrybucja dojrzewa ~7 dni)
DEFAULT_LOOKBACK_DAYS = 14
# Maksymalna retencja danych po stronie Amazona
MAX_HISTORY_DAYS = 95

# Pobieramy tylko to, czym da sie jeszcze zarzadzac. Archiwum w Amazonie jest
# nieodwracalne - zarchiwizowanej kampanii nie da sie wznowic ani edytowac,
# wiec sciaganie jej to czysty balast. Na koncie usera archiwum stanowilo
# 99,7% targetow (170 tys. z 170,6 tys.).
ACTIVE_STATES = ["ENABLED", "PAUSED"]


# --------------------------------------------------------------------------- #
# Pomocnicze
# --------------------------------------------------------------------------- #


def _f(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "null"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    if value in (None, "", "null"):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _s(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _date(value: Any) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    text = str(value)[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Wspolne metryki raportowe -> kolumny MetricsMixin."""
    return {
        "impressions": _i(row.get("impressions")),
        "clicks": _i(row.get("clicks")),
        "cost": _f(row.get("cost", row.get("spend"))),
        "orders": _i(row.get("purchases7d")),
        "sales": _f(row.get("sales7d")),
        "units": _i(row.get("unitsSoldClicks7d")),
        "orders_same_sku": _i(row.get("purchasesSameSku7d")),
        "sales_same_sku": _f(row.get("attributedSalesSameSku7d")),
        "tos_impression_share": _pct(row.get("topOfSearchImpressionShare")),
        # Puste tam, gdzie Amazon tej kolumny nie oddaje (kampanie, placementy) -
        # None, a nie zero, bo brak danych to co innego niz brak efektu halo.
        "sales_other_sku": _opt_f(row.get("salesOtherSku7d")),
        "units_other_sku": _opt_i(row.get("unitsSoldOtherSku7d")),
    }


def _opt_f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _opt_i(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _pct(value: Any) -> float | None:
    """Udzial wyswietlen jako 0-100; brak danych zostaje None."""
    if value in (None, ""):
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


# SQLite ma twardy limit parametrow w jednym zapytaniu. Przy kilku tysiacach
# reklam produktowych lista IN (...) go przekracza i konczy sie bledem
# "too many SQL variables", wiec pytamy partiami.
IN_CHUNK = 500


def _upsert(session: Session, model, rows: list[dict[str, Any]], pk: str) -> int:
    """Prosty upsert dzialajacy tak samo na SQLite i Postgresie."""
    if not rows:
        return 0

    column = getattr(model, pk)
    keys = [r[pk] for r in rows if r.get(pk) is not None]

    existing: set[Any] = set()
    for i in range(0, len(keys), IN_CHUNK):
        existing.update(
            session.execute(
                select(column).where(column.in_(keys[i : i + IN_CHUNK]))
            ).scalars()
        )

    to_insert = [r for r in rows if r.get(pk) is not None and r[pk] not in existing]
    to_update = [r for r in rows if r.get(pk) is not None and r[pk] in existing]

    if to_insert:
        session.bulk_insert_mappings(model, to_insert)
    if to_update:
        session.bulk_update_mappings(model, to_update)
    return len(to_insert) + len(to_update)


def _mark_archived(
    session: Session, model, profile_id: int, seen: set[str], pk: str,
    ad_product: str | None = None,
) -> int:
    """Oznacza jako ARCHIVED rekordy, ktorych Amazon juz nie zwraca.

    Skoro pobieramy tylko ENABLED i PAUSED, zarchiwizowanie czegos objawia sie
    zniknieciem z odpowiedzi. Zamiast kasowac wiersz - oznaczamy go: metryki
    historyczne odwoluja sie do niego po identyfikatorze i bez tego wiersza
    tabela pokazywalaby puste nazwy dla wydatkow sprzed archiwizacji.

    `ad_product` jest **konieczny**, odkad w tych samych tabelach siedzą
    kampanie SP i SB: bez niego sync jednego typu archiwizowalby wszystkie
    kampanie drugiego, bo nie ma ich we wlasnej liscie.
    """
    if not seen:
        return 0

    # notin_ z dziesiatkami tysiecy wartosci przekracza limit parametrow SQLite,
    # wiec roznice liczymy po stronie Pythona
    column = getattr(model, pk)
    warunki = [model.profile_id == profile_id, model.state != "ARCHIVED"]
    if ad_product is not None and hasattr(model, "ad_product"):
        warunki.append(model.ad_product == ad_product)
    existing = session.execute(select(column).where(*warunki)).scalars().all()
    missing = [x for x in existing if x not in seen]
    if not missing:
        return 0
    for i in range(0, len(missing), IN_CHUNK):
        session.execute(
            update(model)
            .where(column.in_(missing[i : i + IN_CHUNK]))
            .values(state="ARCHIVED")
        )
    return len(missing)


def _replace_range(
    session: Session, model, profile_id: int, start: dt.date, end: dt.date,
    rows: list[dict[str, Any]], ad_product: str = "SP",
) -> int:
    """Kasuje fakty w oknie i wstawia swieze.

    `ad_product` jest **konieczny**, odkad w tych samych tabelach leza metryki
    SP, SB i SD: bez niego zapis jednego typu kasowalby dane pozostalych za
    ten sam okres.
    """
    warunki = [model.profile_id == profile_id, model.date >= start, model.date <= end]
    if hasattr(model, "ad_product"):
        warunki.append(model.ad_product == ad_product)
    session.execute(delete(model).where(and_(*warunki)))
    # Upsert, nie zwykly insert - gdyby mimo blokady dwa przebiegi jednak
    # zapisywaly ten sam zakres, wygrywa ostatni zamiast wywracac raport.
    return wstaw_lub_zastap(session, model, rows)


def _dedupe(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Raporty potrafia zwrocic ten sam klucz dwa razy - sumujemy metryki."""
    merged: dict[tuple, dict[str, Any]] = {}
    metric_cols = (
        "impressions", "clicks", "cost", "orders", "sales", "units",
        "orders_same_sku", "sales_same_sku",
    )
    for row in rows:
        k = tuple(row.get(key) for key in keys)
        if k in merged:
            for col in metric_cols:
                merged[k][col] = merged[k].get(col, 0) + row.get(col, 0)
        else:
            merged[k] = dict(row)
    return list(merged.values())


# --------------------------------------------------------------------------- #
# Sync profili
# --------------------------------------------------------------------------- #


def sync_profiles() -> list[dict[str, Any]]:
    raw = list_profiles()
    rows = []
    for p in raw:
        info = p.get("accountInfo") or {}
        rows.append(
            {
                "profile_id": int(p["profileId"]),
                "country_code": p.get("countryCode"),
                "currency_code": p.get("currencyCode"),
                "timezone": p.get("timezone"),
                "daily_budget": _f(p.get("dailyBudget"), 0.0) or None,
                "marketplace_id": info.get("marketplaceStringId"),
                "account_name": info.get("name"),
                "account_type": info.get("type"),
                "entity_id": info.get("id"),
                "is_active": True,
            }
        )

    with session_scope() as session:
        _upsert(session, Profile, rows, "profile_id")

    log.info("Zsynchronizowano %s profili", len(rows))
    return rows


# --------------------------------------------------------------------------- #
# Sync encji
# --------------------------------------------------------------------------- #


def sync_entities(profile_id: int) -> dict[str, int]:
    """Pobiera pelen stan konta SP dla jednego profilu."""
    counts: dict[str, int] = {}
    pid = int(profile_id)

    with AdsClient(profile_id=pid) as client, session_scope() as session:
        run = SyncRun(kind="entities", profile_id=pid)
        session.add(run)
        session.flush()

        # --- portfolia ---
        try:
            portfolios = entities.list_portfolios(client)
            rows = [
                {
                    "portfolio_id": int(p["portfolioId"]),
                    "profile_id": pid,
                    "name": p.get("name"),
                    "state": p.get("state"),
                    # Przy policy NO_CAP Amazon nie zwraca kwoty - to nie brak danych,
                    # tylko portfolio bez limitu budzetu
                    "budget_amount": _f((p.get("budget") or {}).get("amount"), 0.0) or None,
                    "budget_policy": (p.get("budget") or {}).get("policy"),
                }
                for p in portfolios
            ]
            counts["portfolios"] = _upsert(session, Portfolio, rows, "portfolio_id")
        except Exception as exc:  # portfolia bywaja niedostepne na czesci kont
            log.warning("Pominieto portfolia: %s", exc)
            counts["portfolios"] = 0

        # --- kampanie ---
        campaigns = entities.list_resource(client, "campaign", state_filter=ACTIVE_STATES)
        rows = []
        for c in campaigns:
            budget = c.get("budget") or {}
            rows.append(
                {
                    "campaign_id": str(c["campaignId"]),
                    "profile_id": pid,
                    "ad_product": "SP",
                    "portfolio_id": _i(c.get("portfolioId")) or None,
                    "name": c.get("name"),
                    "state": c.get("state"),
                    "targeting_type": c.get("targetingType"),
                    "budget_amount": _f(budget.get("budget")),
                    "budget_type": budget.get("budgetType"),
                    "bidding_strategy": (c.get("dynamicBidding") or {}).get("strategy"),
                    "placement_bidding": (c.get("dynamicBidding") or {}).get(
                        "placementBidding"
                    ),
                    "start_date": _date(c.get("startDate")),
                    "end_date": _date(c.get("endDate")),
                    "raw": c,
                }
            )
        counts["campaigns"] = _upsert(session, Campaign, rows, "campaign_id")
        counts["campaigns_archived"] = _mark_archived(
            session, Campaign, pid, {r["campaign_id"] for r in rows}, "campaign_id",
            ad_product="SP",
        )

        # --- grupy reklam ---
        ad_groups = entities.list_resource(client, "adGroup", state_filter=ACTIVE_STATES)
        rows = [
            {
                "ad_group_id": str(g["adGroupId"]),
                "campaign_id": str(g["campaignId"]),
                "profile_id": pid,
                "ad_product": "SP",
                "name": g.get("name"),
                "state": g.get("state"),
                "default_bid": _f(g.get("defaultBid")),
                "raw": g,
            }
            for g in ad_groups
        ]
        counts["ad_groups"] = _upsert(session, AdGroup, rows, "ad_group_id")
        counts["ad_groups_archived"] = _mark_archived(
            session, AdGroup, pid, {r["ad_group_id"] for r in rows}, "ad_group_id",
            ad_product="SP",
        )

        # --- keywordy ---
        keywords = entities.list_resource(client, "keyword", state_filter=ACTIVE_STATES)
        rows = [
            {
                "keyword_id": str(k["keywordId"]),
                "ad_group_id": str(k["adGroupId"]),
                "campaign_id": str(k["campaignId"]),
                "profile_id": pid,
                "ad_product": "SP",
                "keyword_text": k.get("keywordText"),
                "match_type": k.get("matchType"),
                "state": k.get("state"),
                "bid": _f(k.get("bid")) or None,
                "raw": k,
            }
            for k in keywords
        ]
        counts["keywords"] = _upsert(session, Keyword, rows, "keyword_id")
        counts["keywords_archived"] = _mark_archived(
            session, Keyword, pid, {r["keyword_id"] for r in rows}, "keyword_id",
            ad_product="SP",
        )

        # --- targety produktowe / auto ---
        targets = entities.list_resource(client, "target", state_filter=ACTIVE_STATES)
        rows = []
        for t in targets:
            expr = t.get("expression") or t.get("resolvedExpression")
            rows.append(
                {
                    "target_id": str(t["targetId"]),
                    "ad_group_id": str(t["adGroupId"]),
                    "campaign_id": str(t["campaignId"]),
                    "profile_id": pid,
                    "ad_product": "SP",
                    "expression_type": t.get("expressionType"),
                    "expression": expr,
                    "resolved_expression": json.dumps(
                        t.get("resolvedExpression") or expr, ensure_ascii=False
                    )[:4000],
                    "state": t.get("state"),
                    "bid": _f(t.get("bid")) or None,
                    "raw": t,
                }
            )
        counts["targets"] = _upsert(session, Target, rows, "target_id")
        counts["targets_archived"] = _mark_archived(
            session, Target, pid, {r["target_id"] for r in rows}, "target_id",
            ad_product="SP",
        )

        # --- negatywy ---
        neg_rows: list[dict[str, Any]] = []
        for resource, level in (("negativeKeyword", "AD_GROUP"),
                                ("campaignNegativeKeyword", "CAMPAIGN")):
            try:
                for n in entities.list_resource(client, resource, state_filter=ACTIVE_STATES):
                    neg_rows.append(
                        {
                            "keyword_id": str(n["keywordId"]),
                            "ad_group_id": _s(n.get("adGroupId")),
                            "campaign_id": str(n["campaignId"]),
                            "profile_id": pid,
                            "level": level,
                            "keyword_text": n.get("keywordText"),
                            "match_type": n.get("matchType"),
                            "state": n.get("state"),
                        }
                    )
            except Exception as exc:
                log.warning("Pominieto %s: %s", resource, exc)
        counts["negative_keywords"] = _upsert(session, NegativeKeyword, neg_rows, "keyword_id")

        # --- wykluczone ASIN-y / marki (negative targeting) ---
        neg_t: list[dict[str, Any]] = []
        try:
            for n in entities.list_resource(client, "negativeTarget",
                                            state_filter=ACTIVE_STATES):
                expr = n.get("expression") or []
                # Amazon zwraca liste warunkow; sklejamy je w jeden czytelny opis
                readable = ", ".join(
                    f"{e.get('type', '?')} {e.get('value', '')}".strip() for e in expr
                ) if isinstance(expr, list) else str(expr)
                # Amazon nie zwraca expressionType dla negatywnych targetow -
                # typ trzeba odczytac z samego wyrazenia
                first = expr[0] if isinstance(expr, list) and expr else {}
                kind = first.get("type") if isinstance(first, dict) else None

                neg_t.append({
                    "target_id": str(n["targetId"]),
                    "ad_group_id": _s(n.get("adGroupId")),
                    "campaign_id": str(n["campaignId"]),
                    "profile_id": pid,
                    "level": "AD_GROUP" if n.get("adGroupId") else "CAMPAIGN",
                    "expression_type": kind,
                    "value": readable[:250],
                    "state": n.get("state"),
                    "raw": n,
                })
        except Exception as exc:
            log.warning("Pominieto negatywne targety: %s", exc)
        counts["negative_targets"] = _upsert(session, NegativeTarget, neg_t, "target_id")

        # --- reklamy produktowe ---
        product_ads = entities.list_resource(client, "productAd", state_filter=ACTIVE_STATES)
        rows = [
            {
                "ad_id": str(a["adId"]),
                "ad_product": "SP",
                "ad_group_id": str(a["adGroupId"]),
                "campaign_id": str(a["campaignId"]),
                "profile_id": pid,
                "asin": a.get("asin"),
                "sku": a.get("sku"),
                "state": a.get("state"),
            }
            for a in product_ads
        ]
        counts["product_ads"] = _upsert(session, ProductAd, rows, "ad_id")

        run.status = "OK"
        run.finished_at = utcnow()
        run.rows = sum(counts.values())
        run.detail = json.dumps(counts)

    log.info("Encje profilu %s: %s", pid, counts)
    return counts


# --------------------------------------------------------------------------- #
# Sync raportow
# --------------------------------------------------------------------------- #


# --- mapowanie surowych wierszy raportu na kolumny faktow ---


def _rows_campaign(pid, raw):
    return _dedupe([
        {"date": _date(r.get("date")), "profile_id": pid,
         "campaign_id": _s(r.get("campaignId")), "ad_product": "SP", **_metrics(r)}
        for r in raw if r.get("campaignId") and _date(r.get("date"))
    ], ("date", "profile_id", "campaign_id", "ad_product"))


# Raport zwraca nazwy czytelne dla czlowieka, a API zapisu przyjmuje enumy.
# Normalizujemy przy zapisie, zeby w bazie byla jedna postac - inaczej reguly
# porownywalyby "Top of Search on-Amazon" z "PLACEMENT_TOP" i nigdy nie trafily.
PLACEMENT_MAP = {
    "top of search on-amazon": "PLACEMENT_TOP",
    "detail page on-amazon": "PLACEMENT_PRODUCT_PAGE",
    "other on-amazon": "PLACEMENT_REST_OF_SEARCH",
}


def _placement(value: Any) -> str:
    if not value:
        return "UNKNOWN"
    text = str(value).strip()
    # Wartosc juz w postaci enuma (np. z wczesniejszego syncu) zostaw bez zmian
    if text.upper().startswith("PLACEMENT_"):
        return text.upper()
    mapped = PLACEMENT_MAP.get(text.lower())
    if mapped:
        return mapped
    log.warning("Nieznany placement w raporcie: %r - zapisuje bez zmiany", text)
    return text


def _rows_placement(pid, raw):
    return _dedupe([
        {"date": _date(r.get("date")), "profile_id": pid,
         "campaign_id": _s(r.get("campaignId")),
         "placement": _placement(r.get("placementClassification")), **_metrics(r)}
        for r in raw if r.get("campaignId") and _date(r.get("date"))
    ], ("date", "profile_id", "campaign_id", "placement"))


def _rows_targeting(pid, raw):
    rows = []
    for r in raw:
        target_id = _s(r.get("keywordId")) or _s(r.get("targetId"))
        if not target_id or not _date(r.get("date")):
            continue
        rows.append({
            "date": _date(r.get("date")), "profile_id": pid,
            "campaign_id": _s(r.get("campaignId")), "ad_group_id": _s(r.get("adGroupId")),
            "target_id": target_id,
            # Rodzaj wynika z match_type, nie z obecnosci pola keyword -
            # Amazon wypelnia keyword takze dla auto-targetow ("close-match")
            "target_kind": targeting_names.target_kind(r.get("matchType")),
            "target_text": r.get("keyword") or r.get("targeting"),
            "match_type": r.get("matchType"),
            "bid": _f(r.get("keywordBid")) or None,
            "ad_product": "SP",
            **_metrics(r),
        })
    return _dedupe(rows, ("date", "profile_id", "target_id"))


def _rows_search_term(pid, raw):
    return _dedupe([
        {"date": _date(r.get("date")), "profile_id": pid,
         "campaign_id": _s(r.get("campaignId")), "ad_group_id": _s(r.get("adGroupId")),
         "target_id": _s(r.get("keywordId")),
         "search_term": (r.get("searchTerm") or "")[:500],
         "target_text": r.get("keyword") or r.get("targeting"),
         "match_type": r.get("matchType"), "ad_product": "SP", **_metrics(r)}
        for r in raw if r.get("searchTerm") and _date(r.get("date"))
    ], ("date", "profile_id", "campaign_id", "target_id", "search_term"))


def _rows_product(pid, raw):
    return _dedupe([
        {"date": _date(r.get("date")), "profile_id": pid,
         "campaign_id": _s(r.get("campaignId")), "ad_group_id": _s(r.get("adGroupId")),
         "asin": _s(r.get("advertisedAsin")), "sku": _s(r.get("advertisedSku")),
         "ad_product": "SP", **_metrics(r)}
        for r in raw if _date(r.get("date"))
    ], ("date", "profile_id", "campaign_id", "ad_group_id", "sku", "asin"))


REPORT_KINDS: dict[str, tuple[Any, Any]] = {
    "campaign":    (CampaignDaily,   _rows_campaign),
    "placement":   (PlacementDaily,  _rows_placement),
    "targeting":   (TargetDaily,     _rows_targeting),
    "search_term": (SearchTermDaily, _rows_search_term),
    "product":     (ProductDaily,    _rows_product),
}


# Ile raportow zlecac naraz. Waskim gardlem jest generowanie po stronie Amazona,
# nie nasz procesor - ale zbyt duzy naraz burst grozi limitem zapytan (429).
# Szesc to kompromis: 95 dni jednego rynku (20 raportow) schodzi w ~4 turach.
REPORT_WORKERS = 6


def sync_reports(
    profile_id: int,
    start: dt.date | None = None,
    end: dt.date | None = None,
    kinds: list[str] | None = None,
    workers: int = REPORT_WORKERS,
) -> dict[str, Any]:
    """Pobiera raporty dzienne i przepisuje fakty dla podanego okna.

    Raporty pobieramy rownolegle - kazde okno kazdego typu to osobne zadanie.
    Zapis do bazy zostaje sekwencyjny i w osobnej transakcji na typ raportu:
    gdy jeden typ zawiedzie, reszta i tak wyladuje w bazie.
    """
    pid = int(profile_id)
    today = dt.date.today()
    end = end or today - dt.timedelta(days=1)
    start = start or end - dt.timedelta(days=DEFAULT_LOOKBACK_DAYS - 1)
    start = max(start, today - dt.timedelta(days=MAX_HISTORY_DAYS))
    kinds = kinds or list(REPORT_KINDS)

    tasks = [
        (kind, win_start, win_end)
        for kind in kinds
        if kind in REPORT_KINDS
        for win_start, win_end in reports.date_windows(start, end)
    ]
    unknown = [k for k in kinds if k not in REPORT_KINDS]

    # Trzymamy wiersze osobno per okno, a nie w jednym worku na typ raportu.
    # Dzieki temu nieudane okno kosztuje tylko swoj kawalek historii - reszta
    # zakresu ladnie sie zapisze (patrz komentarz przy zapisie nizej).
    raw_by_window: dict[tuple[str, dt.date, dt.date], list[dict[str, Any]]] = {}
    failed_windows: dict[str, list[str]] = {}
    errors: dict[str, str] = {k: "nieznany typ raportu" for k in unknown}

    log.info(
        "Raporty profilu %s: %s zadan (%s..%s), rownolegle po %s",
        pid, len(tasks), start, end, workers,
    )

    with AdsClient(profile_id=pid) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(reports.fetch_window, client, kind, ws, we): (kind, ws, we)
                for kind, ws, we in tasks
            }
            for future in as_completed(futures):
                kind, ws, we = futures[future]
                try:
                    raw_by_window[(kind, ws, we)] = future.result()
                except Exception as exc:
                    log.exception("Raport %s %s..%s (profil %s) nie powiodl sie",
                                  kind, ws, we, pid)
                    failed_windows.setdefault(kind, []).append(
                        f"{ws}..{we}: {str(exc)[:160]}"
                    )

    counts: dict[str, int] = {}
    for kind in (k for k in kinds if k in REPORT_KINDS):
        # Zapisujemy okno po oknie. _replace_range czysci przedzial dat przed
        # wstawieniem, wiec wolno mu oddac tylko te przedzialy, ktore naprawde
        # pobralismy - inaczej nieudane okno skasowaloby dane, ktorych nie mamy
        # czym zastapic. Okna sa rozlaczne, wiec te udane zapisuja sie w calosci
        # niezaleznie od tego, ze sasiadowi nie wyszlo.
        windows = sorted(
            (ws, we) for (k, ws, we) in raw_by_window if k == kind
        )
        if not windows:
            errors[kind] = "; ".join(failed_windows.get(kind, ["brak danych"]))[:300]
            continue

        model, builder = REPORT_KINDS[kind]
        try:
            written = 0
            with session_scope() as session:
                for ws, we in windows:
                    rows = builder(pid, raw_by_window[(kind, ws, we)])
                    written += _replace_range(session, model, pid, ws, we, rows)
            counts[kind] = written
            log.info(
                "Raport %s (profil %s): %s wierszy z %s okien%s",
                kind, pid, written, len(windows),
                f" (nieudanych: {len(failed_windows[kind])})" if kind in failed_windows else "",
            )
        except Exception as exc:
            log.exception("Zapis raportu %s (profil %s) nie powiodl sie", kind, pid)
            failed_windows.setdefault(kind, []).append(str(exc)[:160])

        if kind in failed_windows:
            errors[kind] = "; ".join(failed_windows[kind])[:300]

    with session_scope() as session:
        session.add(SyncRun(
            kind="reports",
            profile_id=pid,
            status="OK" if not errors else ("FAILED" if not counts else "PARTIAL"),
            finished_at=utcnow(),
            rows=sum(counts.values()),
            detail=json.dumps(
                {"window": [start.isoformat(), end.isoformat()], **counts,
                 **({"errors": errors} if errors else {})},
                ensure_ascii=False,
            ),
        ))

    log.info("Raporty profilu %s (%s..%s): %s%s", pid, start, end, counts,
             f" BLEDY: {list(errors)}" if errors else "")
    return {**counts, **({"errors": errors} if errors else {})}


def sync_all(
    profile_ids: list[int] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    with_reports: bool = True,
) -> dict[str, Any]:
    """Pelny sync: profile -> encje -> raporty reklamowe -> sprzedaz i FBA.

    Sprzedaz calkowita i podzial FBA sa czescia tego przebiegu, nie osobnym
    zadaniem: przycisk "Sync" ma dociagnac WSZYSTKO, co panel pokazuje.
    Wczesniej pobieral tylko reklamy, wiec TACOS i udzial FBA zostawaly w
    tyle az do najblizszego odswiezenia planowego - a to mylilo, bo data
    pod wykresem sprzedazy nie zmieniala sie po recznym kliknieciu.

    Okno sprzedazy jest to samo co reklam. Jeden raport na rynek niezaleznie
    od dlugosci okna (limit to 60 dni), wiec 14 dni kosztuje tyle samo
    zapytan co 3 - nie ma powodu prosic o mniej.
    """
    from ..config import settings

    sync_profiles()

    with session_scope() as session:
        available = list(session.execute(select(Profile.profile_id)).scalars())

    targets = profile_ids or available
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=lookback_days - 1)

    summary: dict[str, Any] = {}
    for pid in targets:
        entry: dict[str, Any] = {}
        try:
            entry["entities"] = sync_entities(pid)
            if with_reports:
                entry["reports"] = sync_reports(pid, start, end)
        except Exception as exc:
            log.exception("Sync profilu %s nie powiodl sie", pid)
            entry["error"] = str(exc)
        summary[str(pid)] = entry

    # Sprzedaz calkowita (mianownik TACOS) i podzial FBA - tylko gdy SP-API
    # jest skonfigurowane. Bez kluczy te dwa zrodla po prostu nie istnieja
    # i panel dziala na samych reklamach.
    if with_reports and settings.sp_configured:
        from . import orders_sync, sales_sync

        # Przy sync jednego rynku ciagniemy sprzedaz tylko dla niego; raport
        # zamowien i tak obejmuje cale konto, wiec dzielenie go nie ma sensu.
        jeden = targets[0] if profile_ids and len(profile_ids) == 1 else None
        try:
            summary["total_sales"] = sales_sync.sync_all(start, end, jeden)
        except Exception as exc:
            log.exception("Sync sprzedazy calkowitej nie powiodl sie")
            summary["total_sales"] = {"error": str(exc)[:300]}
        try:
            summary["fulfillment"] = orders_sync.sync_window(start, end)
        except Exception as exc:
            log.exception("Sync podzialu FBA nie powiodl sie")
            summary["fulfillment"] = {"error": str(exc)[:300]}

    return summary

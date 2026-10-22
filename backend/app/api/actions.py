"""Zapis zmian do Amazona + audyt w change_log.

ZASADA NACZELNA: zadna zmiana nie trafia do Amazona bez wczesniejszego
zatwierdzenia przez czlowieka. Nie ma flagi, ktora to omija.

Przeplyw jest dwustopniowy i wymuszony po stronie serwera:

  1. Endpointy akcji (bids / state / budgets / negatives / harvest) LICZA plan,
     zapisuja go w tabeli `pending_plans` i zwracaja `plan_id`. Nic nie wysylaja.
  2. Dopiero `apply_plan(plan_id)` wykonuje dokladnie to, co bylo w podgladzie.

Funkcje wykonujace faktyczny zapis (`_execute_*`) sa prywatne i wolane wylacznie
z `apply_plan`. Publiczne API nie daje sposobu, by je wywolac bezposrednio -
wiec ani interfejs, ani przypadkowe zapytanie curl-em nie nadpisze kampanii
bez podgladu. Dotyczy to tak samo zmian recznych, jak i tych z regul.

Plan czeka w kolejce do decyzji i NIE wygasa. Wczesniej znikal po kwadransie,
przez co praca przerwana na chwile przepadala. Sugestia wyslana do kolejki
znika z listy sugestii, zeby ta sama zmiana nie wisiala w dwoch miejscach.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..ads import entities
from ..ads.client import AdsClient
from ..models import (
    AdGroup, Campaign, ChangeLog, Keyword, NegativeKeyword, NegativeTarget,
    PendingPlan, Portfolio, Profile, ProductAd, Target,
)
from ..timeutil import iso_utc, utcnow
from . import targeting_names

log = logging.getLogger(__name__)

MIN_BID = 0.02
MAX_BID = 100.0

# Kazdy produkt reklamowy ma wlasny dopuszczalny zakres stawki. Sponsored
# Display zaczyna sie od 1,00 EUR - sugestia na 0,85 wracala z Amazona jako
# "Bid is out of range (must be in [1.0, 1000.0])", a plan wygladal na
# wykonalny az do zatwierdzenia. Wartosci potwierdzone na komunikatach API.
ZAKRESY_STAWEK = {
    "SP": (0.02, 100.0),
    "SB": (0.10, 100.0),
    "SD": (1.00, 100.0),
}


def zakres_stawki(ad_product: str | None) -> tuple[float, float]:
    return ZAKRESY_STAWEK.get(ad_product or "SP", (MIN_BID, MAX_BID))
PLAN_TTL_MINUTES = settings.plan_ttl_minutes


class PlanError(RuntimeError):
    """Plan nie istnieje, wygasl albo zostal juz uzyty."""


# --------------------------------------------------------------------------- #
# Audyt
# --------------------------------------------------------------------------- #


def _log_change(
    session: Session,
    profile_id: int,
    entity_type: str,
    entity_id: str,
    field: str,
    old: Any,
    new: Any,
    source: str,
    status: str,
    rule_id: int | None = None,
    error: str | None = None,
) -> None:
    session.add(
        ChangeLog(
            profile_id=profile_id,
            entity_type=entity_type,
            entity_id=str(entity_id),
            field=field,
            old_value=None if old is None else str(old),
            new_value=None if new is None else str(new),
            source=source,
            status=status,
            rule_id=rule_id,
            error=error,
        )
    )


def _split_results(result: dict[str, list[dict]], id_field: str) -> tuple[set[str], dict[str, str]]:
    ok = {str(r.get(id_field)) for r in result.get("success", []) if r.get(id_field)}
    errors: dict[str, str] = {}
    for r in result.get("error", []):
        key = str(r.get(id_field) or r.get("index") or "?")
        detail = r.get("errors") or r.get("message") or r
        errors[key] = str(detail)[:500]
    return ok, errors


# --------------------------------------------------------------------------- #
# Plany
# --------------------------------------------------------------------------- #


def _create_plan(
    session: Session,
    profile_id: int,
    kind: str,
    summary: str,
    payload: dict[str, Any],
    planned: list[dict[str, Any]],
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """Zapisuje policzony plan i zwraca go razem z identyfikatorem do zatwierdzenia."""
    now = utcnow()
    # Klucze sugestii dla kazdego planu, nie tylko dla tych z listy sugestii.
    # Zmiana zrobiona z tabeli (zaznacz -> wstrzymaj / nowa stawka) tez ma
    # uciszyc odpowiadajaca jej sugestie: trwala na zawsze, strojaca na
    # tydzien. Kolejka sugestii nadpisuje te klucze swoimi, gdy je ma.
    if "suggestion_keys" not in payload:
        payload = {**payload, "suggestion_keys": _klucze_z_planu(kind, payload, planned)}
    plan = PendingPlan(
        id=str(uuid.uuid4()),
        profile_id=profile_id,
        kind=kind,
        summary=summary,
        payload={**payload, "planned": planned, "source": source, "rule_id": rule_id},
        item_count=len(planned),
        status="PENDING",
        expires_at=now + dt.timedelta(minutes=PLAN_TTL_MINUTES),
    )
    session.add(plan)

    for item in planned:
        _log_change(
            session, profile_id, item.get("entity_type", kind.upper()), str(item.get("id", "?")),
            item.get("field", "?"), item.get("old"), item.get("new"),
            source, "DRY_RUN", rule_id,
        )

    log.info("Plan %s (%s): %s pozycji", plan.id, kind, len(planned))
    names = _entity_names(session, planned)
    context = _entity_context(session, planned)
    return {
        "requires_confirmation": True,
        "plan_id": plan.id,
        "kind": kind,
        "summary": summary,
        "planned": [_describe_item(item, names, context) for item in planned],
        "item_count": len(planned),
        "expires_at": iso_utc(plan.expires_at),
        "applied": 0,
        "failed": 0,
    }


def _klucze_z_planu(kind: str, payload: dict[str, Any], planned: list[dict[str, Any]]) -> list[str]:
    """Odtwarza klucze sugestii z pozycji planu - ten sam ksztalt, ktory
    daje `action_key` dla akcji z listy sugestii, wiec lista sugestii
    rozpozna zmiane niezaleznie od tego, ktora droga weszla do kolejki."""
    out: list[str] = []
    for p in planned:
        it = p.get("item") or {}
        a: dict[str, Any] | None = None
        if kind == "bids":
            a = {"type": "SET_BID", "target_id": p.get("id")}
        elif kind == "budgets":
            a = {"type": "SET_BUDGET", "campaign_id": p.get("id")}
        elif kind == "placements":
            a = {"type": "SET_PLACEMENT", "campaign_id": p.get("id"),
                 "placement": p.get("placement")}
        elif kind == "state" and str(payload.get("state", "")).upper() == "PAUSED" \
                and payload.get("entity_type") in ("KEYWORD", "TARGET"):
            a = {"type": "PAUSE_TARGET", "target_id": p.get("id")}
        elif kind == "product_ads" and str(payload.get("state", "")).upper() == "PAUSED":
            a = {"type": "PAUSE_PRODUCT", "ad_id": p.get("id")}
        elif kind == "negatives":
            a = {"type": "ADD_NEGATIVE", "campaign_id": it.get("campaignId"),
                 "ad_group_id": it.get("adGroupId"), "keyword_text": it.get("keywordText")}
        elif kind == "negative_targets":
            a = {"type": "ADD_NEGATIVE_ASIN", "campaign_id": it.get("campaignId"),
                 "ad_group_id": it.get("adGroupId"), "asin": it.get("asin")}
        if a:
            out.append(action_key(a))
    return out


def action_key(a: dict[str, Any]) -> str:
    """Trwaly identyfikator sugestii, ten sam po obu stronach.

    Sluzy do tego, zeby sugestia juz wyslana do kolejki znikala z listy
    sugestii: plan zapamietuje klucze pozycji, ktore go utworzyly, a lista
    sugestii je pomija. Bez tego ta sama zmiana wisi w dwoch miejscach naraz
    i latwo wyslac ja drugi raz.

    Klucz musi byc liczony z samej TRESCI akcji, bo plan nie zna wiersza,
    z ktorego sugestia powstala.
    """
    typ = str(a.get("type") or "")
    czesci = [
        typ,
        str(a.get("target_id") or ""),
        str(a.get("campaign_id") or ""),
        str(a.get("ad_group_id") or ""),
        str(a.get("keyword_text") or "").strip().lower(),
        str(a.get("asin") or "").upper(),
        str(a.get("ad_id") or ""),
        str(a.get("placement") or ""),
    ]
    return "|".join(czesci)


# Akcje, ktore zmieniaja stan konta na trwale. Po ich wykonaniu sugestia nie
# ma prawa wrocic: fraza jest wykluczona, keyword dodany, produkt wstrzymany.
# Wracala, bo dane w raportach zmieniaja sie dopiero przy nastepnym pobraniu,
# a lokalna kopia encji nie wie o zmianie az do synchronizacji.
AKCJE_TRWALE = {
    "ADD_NEGATIVE", "ADD_NEGATIVE_ASIN", "HARVEST", "PAUSE_TARGET", "PAUSE_PRODUCT",
}

# Akcje strojace (stawki, budzety, mnozniki placementow) moga wrocic - ale nie
# od razu. Tydzien to ten sam prog, po ktorym raport skutecznosci potrafi
# ocenic poprzednia zmiane; proponowanie kolejnej korekty, zanim widac skutek
# pierwszej, to strzelanie na oslep.
KARENCJA_STROJENIA_DNI = 7


def queued_action_keys(session: Session, profile_id: int | None = None) -> set[str]:
    """Klucze sugestii, ktorych nie ma juz sensu pokazywac.

    Obejmuje dwa przypadki:

    * **czekajace w kolejce** - inaczej ta sama zmiana wisialaby w dwoch
      miejscach naraz i latwo byloby wyslac ja drugi raz,
    * **juz zatwierdzone** - wczesniej filtr patrzyl wylacznie na plany
      PENDING, wiec w chwili zatwierdzenia klucz z niego znikal i sugestia
      wracala na liste. Wygladalo to, jakby zatwierdzenie nic nie dalo.

    Zatwierdzone traktujemy roznie zaleznie od rodzaju akcji: jednorazowe
    znikaja na stale, strojace wracaja po karencji.
    """
    conds = [PendingPlan.status.in_(["PENDING", "APPLIED"])]
    if profile_id:
        conds.append(PendingPlan.profile_id == profile_id)

    granica = utcnow() - dt.timedelta(days=KARENCJA_STROJENIA_DNI)
    out: set[str] = set()

    for plan in session.execute(select(PendingPlan).where(*conds)).scalars():
        klucze = (plan.payload or {}).get("suggestion_keys") or []
        if not klucze:
            continue

        if plan.status == "PENDING":
            out.update(str(k) for k in klucze)
            continue

        swiezy = plan.applied_at is not None and plan.applied_at >= granica
        for k in klucze:
            klucz = str(k)
            # Typ akcji stoi na poczatku klucza (patrz `action_key`).
            typ = klucz.split("|", 1)[0]
            if typ in AKCJE_TRWALE or swiezy:
                out.add(klucz)

    return out


def get_plan(session: Session, plan_id: str) -> PendingPlan:
    plan = session.get(PendingPlan, plan_id)
    if plan is None:
        raise PlanError("Plan nie istnieje. Policz zmiany jeszcze raz.")
    if plan.status != "PENDING":
        raise PlanError(f"Plan zostal juz zamkniety (status {plan.status}).")
    # Plany celowo NIE wygasaja. Wczesniej znikaly po kwadransie, przez co
    # praca przerwana na chwile przepadala. Ochrona przed zatwierdzeniem
    # obliczen na starych danych zostaje, ale inaczej: przy zatwierdzaniu
    # sprawdzamy, czy wartosc wyjsciowa sie nie zmienila (patrz _drifted).
    return plan


def cancel_plan(session: Session, plan_id: str) -> dict[str, Any]:
    """Odrzuca plan - takze wygasly.

    Odrzucenie nigdy niczego nie wysyla, wiec nie ma powodu blokowac go po
    wygasnieciu. Wczesniej wygasly plan zostawal na liscie na zawsze: przycisku
    zatwierdzenia juz nie mial, a odrzucic sie go nie dalo.
    """
    plan = session.get(PendingPlan, plan_id)
    if plan is None:
        raise PlanError("Plan nie istnieje.")
    if plan.status in ("APPLIED", "CANCELLED"):
        raise PlanError(f"Plan zostal juz zamkniety (status {plan.status}).")
    plan.status = "CANCELLED"
    return {"plan_id": plan_id, "status": "CANCELLED"}


def _rebuild_placement_items(
    payload: dict[str, Any], selected: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Odtwarza zestaw placementBidding dla czesciowego zatwierdzenia.

    Amazon nadpisuje caly zestaw naraz, wiec przy zatwierdzeniu tylko czesci
    pozycji te niezatwierdzone musza wrocic do swojej OBECNEJ wartosci -
    inaczej zniknelyby do zera razem z zapisem.
    """
    wanted: dict[str, dict[str, float]] = {}
    for item in selected:
        placement = item.get("placement")
        if placement:
            wanted.setdefault(str(item["id"]), {})[placement] = item.get("new_value", 0.0)

    out = []
    for entry in payload.get("items", []):
        cid = str(entry.get("campaignId"))
        if cid not in wanted:
            continue
        out.append({
            **entry,
            "placements": [
                {
                    "placement": slot["placement"],
                    # zatwierdzone dostaja nowa wartosc, reszta zostaje jak jest
                    "percentage": wanted[cid].get(slot["placement"], slot.get("percentage", 0)),
                }
                for slot in entry.get("placements", [])
            ],
        })
    return out


def apply_plan(
    session: Session, plan_id: str, indexes: list[int] | None = None
) -> dict[str, Any]:
    """Jedyne wejscie do faktycznego zapisu w Amazonie.

    `indexes` pozwala zatwierdzic tylko czesc pozycji planu. Niezatwierdzone
    zostaja w kolejce jako osobny, mniejszy plan - zamiast znikac razem
    z pierwszym zatwierdzeniem.
    """
    plan = get_plan(session, plan_id)
    payload = dict(plan.payload or {})
    all_planned: list[dict[str, Any]] = payload.get("planned") or []
    source = payload.get("source", "MANUAL")
    rule_id = payload.get("rule_id")

    # Utworzenie kampanii jest niepodzielne: grupa reklam nie powstanie bez
    # kampanii, a slowo kluczowe bez grupy. Wybor pojedynczych pozycji nie ma
    # tu sensu i cichie jego przyjecie skonczyloby sie polprodukto na koncie.
    if indexes is not None and plan.kind == "create_campaign":
        raise PlanError(
            "Nowej kampanii nie da się zatwierdzić po kawałku — "
            "grupa reklam i słowa kluczowe powstają wewnątrz kampanii. "
            "Zatwierdź całość albo odrzuć i popraw."
        )

    if indexes is None:
        planned = all_planned
        remaining: list[dict[str, Any]] = []
    else:
        chosen = {i for i in indexes if 0 <= i < len(all_planned)}
        if not chosen:
            raise PlanError("Nie wskazano żadnej pozycji do zatwierdzenia.")
        planned = [item for i, item in enumerate(all_planned) if i in chosen]
        remaining = [item for i, item in enumerate(all_planned) if i not in chosen]
        if plan.kind == "placements":
            payload["items"] = _rebuild_placement_items(payload, planned)

    executors = {
        "bids": _execute_bids,
        "state": _execute_state,
        "budgets": _execute_budgets,
        "negatives": _execute_negatives,
        "harvest": _execute_harvest,
        "placements": _execute_placements,
        "negative_targets": _execute_negative_targets,
        "remove_negatives": _execute_remove_negatives,
        "product_ads": _execute_product_ads,
        "add_products": _execute_add_products,
        "create_campaign": _execute_create_campaign,
    }
    executor = executors.get(plan.kind)
    if executor is None:
        raise PlanError(f"Nieznany typ planu: {plan.kind}")

    result = executor(session, plan.profile_id, payload, planned, source, rule_id)

    if remaining:
        # Reszta zostaje do decyzji - w tym samym planie, z odswiezonym terminem
        plan.payload = {**payload, "planned": remaining}
        plan.item_count = len(remaining)
        # Reszta zostaje w kolejce; nie ruszamy expires_at, bo plany nie wygasaja.
    else:
        plan.status = "APPLIED"
        plan.applied_at = utcnow()

    return {
        "requires_confirmation": False,
        "plan_id": plan_id,
        "kind": plan.kind,
        "summary": plan.summary,
        "remaining": len(remaining),
        **result,
    }


# --------------------------------------------------------------------------- #
# Krok 1: liczenie planow (nic nie wysylaja)
# --------------------------------------------------------------------------- #


def plan_bids(
    session: Session,
    profile_id: int,
    changes: Iterable[dict[str, Any]],
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """changes: [{id, kind: KEYWORD|TARGET, bid}]"""
    changes = list(changes)
    keyword_ids = [str(c["id"]) for c in changes if (c.get("kind") or "KEYWORD").upper() == "KEYWORD"]
    target_ids = [str(c["id"]) for c in changes if (c.get("kind") or "").upper() == "TARGET"]

    old_kw = (
        dict(session.execute(
            select(Keyword.keyword_id, Keyword.bid).where(Keyword.keyword_id.in_(keyword_ids))
        ).all())
        if keyword_ids else {}
    )
    old_tg = (
        dict(session.execute(
            select(Target.target_id, Target.bid).where(Target.target_id.in_(target_ids))
        ).all())
        if target_ids else {}
    )

    # Zakres stawki zalezy od produktu reklamowego, wiec musimy wiedziec,
    # do ktorego nalezy kazda pozycja - inaczej plan proponowalby wartosci,
    # ktore Amazon i tak odrzuci.
    wszystkie_id = [str(c["id"]) for c in changes]
    produkty: dict[str, str] = {}
    for model, kol in ((Keyword, Keyword.keyword_id), (Target, Target.target_id)):
        produkty.update({
            str(i): p or "SP"
            for i, p in session.execute(
                select(kol, model.ad_product).where(kol.in_(wszystkie_id))
            ).all()
        })

    planned = []
    poza_zakresem = 0
    for c in changes:
        kind = (c.get("kind") or "KEYWORD").upper()
        dol, gora = zakres_stawki(produkty.get(str(c["id"])))
        zadana = round(float(c["bid"]), 2)
        new_bid = max(dol, min(gora, zadana))
        if abs(new_bid - zadana) > 0.005:
            poza_zakresem += 1
        old_bid = old_kw.get(str(c["id"])) if kind == "KEYWORD" else old_tg.get(str(c["id"]))
        if old_bid is not None and abs(old_bid - new_bid) < 0.005:
            continue  # nic sie nie zmienia
        planned.append({
            "id": str(c["id"]), "kind": kind, "entity_type": kind,
            "field": "bid", "old": old_bid, "new": new_bid,
            "reason": c.get("reason"),
        })

    opis = f"Zmiana stawek dla {len(planned)} pozycji"
    if poza_zakresem:
        opis += f" — {poza_zakresem} przyciętych do dopuszczalnego zakresu"
    return _create_plan(
        session, profile_id, "bids", opis, {}, planned, source, rule_id,
    )


def plan_state(
    session: Session,
    profile_id: int,
    entity_type: str,
    ids: Iterable[str],
    state: str,
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """entity_type: KEYWORD | TARGET | CAMPAIGN | AD_GROUP.

    state: ENABLED | PAUSED | ARCHIVED.

    Reklamy produktowe maja wlasna sciezke - `plan_product_ads` - bo Amazon
    aktualizuje je innym zasobem i dopuszcza tylko dwa stany.
    """
    ids = [str(i) for i in ids]
    state = state.upper()
    entity_type = entity_type.upper()

    model, pk = {
        "KEYWORD": (Keyword, "keyword_id"),
        "TARGET": (Target, "target_id"),
        "CAMPAIGN": (Campaign, "campaign_id"),
        "AD_GROUP": (AdGroup, "ad_group_id"),
    }[entity_type]

    old_states = dict(
        session.execute(
            select(getattr(model, pk), model.state).where(getattr(model, pk).in_(ids))
        ).all()
    )
    planned = [
        {"id": i, "entity_type": entity_type, "field": "state",
         "old": old_states.get(i), "new": state}
        for i in ids
        if old_states.get(i) != state
    ]

    label = {"PAUSED": "Wstrzymanie", "ENABLED": "Wznowienie", "ARCHIVED": "Archiwizacja"}[state]
    return _create_plan(
        session, profile_id, "state",
        f"{label} {len(planned)} pozycji ({entity_type})",
        {"entity_type": entity_type, "state": state}, planned, source, rule_id,
    )


def plan_budgets(
    session: Session,
    profile_id: int,
    changes: Iterable[dict[str, Any]],
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """changes: [{campaign_id, budget}]"""
    changes = list(changes)
    ids = [str(c["campaign_id"]) for c in changes]
    old = dict(
        session.execute(
            select(Campaign.campaign_id, Campaign.budget_amount).where(
                Campaign.campaign_id.in_(ids)
            )
        ).all()
    )

    planned = []
    for c in changes:
        cid = str(c["campaign_id"])
        new_budget = round(float(c["budget"]), 2)
        if old.get(cid) is not None and abs(old[cid] - new_budget) < 0.005:
            continue
        planned.append({
            "id": cid, "entity_type": "CAMPAIGN", "field": "budget",
            "old": old.get(cid), "new": new_budget, "reason": c.get("reason"),
        })

    return _create_plan(
        session, profile_id, "budgets",
        f"Zmiana budzetu dla {len(planned)} kampanii",
        {}, planned, source, rule_id,
    )


def plan_add_products(
    session: Session,
    profile_id: int,
    ad_group_id: str,
    skus: Iterable[str] = (),
    asins: Iterable[str] = (),
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """Dodanie produktow do istniejacej grupy reklam.

    Pomijamy to, co w grupie juz jest - Amazon i tak odrzucilby duplikat,
    a plan pokazywalby pozycje, ktore nic nie zmieniaja.
    """
    grupa = session.get(AdGroup, str(ad_group_id))
    if grupa is None:
        raise PlanError(f"Nie znam grupy reklam {ad_group_id}")

    juz_sa = {
        (sku, asin)
        for sku, asin in session.execute(
            select(ProductAd.sku, ProductAd.asin).where(
                ProductAd.ad_group_id == str(ad_group_id),
                ProductAd.state != "ARCHIVED",
            )
        ).all()
    }
    obecne_sku = {s for s, _ in juz_sa if s}
    obecne_asin = {a for _, a in juz_sa if a}

    kampania = session.get(Campaign, grupa.campaign_id)
    auto = (kampania.targeting_type or "").upper() == "AUTO" if kampania else False

    planned: list[dict[str, Any]] = []
    for sku in skus:
        sku = str(sku).strip()
        if sku and sku not in obecne_sku:
            planned.append({
                "id": sku, "entity_type": "PRODUCT_AD", "entity_name": sku,
                "field": "product_ad", "old": None, "new": sku,
                "kind": "SKU", "context": grupa.name,
            })
    for asin in asins:
        asin = str(asin).strip().upper()
        if asin and asin not in obecne_asin:
            planned.append({
                "id": asin, "entity_type": "PRODUCT_AD", "entity_name": asin,
                "field": "product_ad", "old": None, "new": asin,
                "kind": "ASIN", "context": grupa.name,
            })

    opis = f"Dodanie {len(planned)} produktow do grupy {grupa.name}"
    if auto:
        # W grupie automatycznej nowy produkt wchodzi do aukcji od razu, po
        # stawce domyslnej grupy - to warto wiedziec przed zatwierdzeniem.
        opis += " (grupa automatyczna - zacznie wydawać od razu)"

    return _create_plan(
        session, profile_id, "add_products", opis,
        {
            "campaign_id": grupa.campaign_id,
            "ad_group_id": str(ad_group_id),
            "ad_group_name": grupa.name,
            "auto": auto,
            "default_bid": grupa.default_bid,
        },
        planned, source, rule_id,
    )


def plan_negatives(
    session: Session,
    profile_id: int,
    items: Iterable[dict[str, Any]],
    *,
    level: str = "AD_GROUP",
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """items: [{campaignId, adGroupId, keywordText, matchType}]"""
    # "item" idzie pozniej prosto do Amazona, wiec uzasadnienie musi z niego
    # wyjsc - to nasze pole, Ads API go nie zna i odrzucilby zadanie.
    items = [dict(it) for it in items]

    # Amazon odrzuca duplikat wykluczenia bledem 207, a plan wygladal na
    # wykonany do momentu zatwierdzenia. Odsiewamy je tutaj, zeby nie
    # proponowac pracy, ktora nie moze sie udac.
    juz_sa = {
        (str(ag or cid), (txt or "").strip().lower(), (mt or "").upper())
        for ag, cid, txt, mt in session.execute(
            select(
                NegativeKeyword.ad_group_id,
                NegativeKeyword.campaign_id,
                NegativeKeyword.keyword_text,
                NegativeKeyword.match_type,
            ).where(NegativeKeyword.state != "ARCHIVED")
        ).all()
    }
    pominiete = 0
    swieze = []
    for it in items:
        klucz = (
            str(it.get("adGroupId") or it["campaignId"]),
            str(it.get("keywordText") or "").strip().lower(),
            str(it.get("matchType") or "NEGATIVE_EXACT").upper(),
        )
        if klucz in juz_sa:
            pominiete += 1
            continue
        swieze.append(it)
    items = swieze

    planned = [
        {
            "id": it.get("adGroupId") or it["campaignId"],
            "entity_type": "NEGATIVE",
            "field": "keywordText",
            "old": None,
            "new": f"{it['keywordText']} ({it.get('matchType', 'NEGATIVE_EXACT')})",
            "reason": it.pop("reason", None),
            "item": it,
        }
        for it in items
    ]
    opis = f"Dodanie {len(planned)} negatywow ({level})"
    if pominiete:
        opis += f" — {pominiete} juz istnieje, pominięto"
    return _create_plan(
        session, profile_id, "negatives", opis,
        {"level": level}, planned, source, rule_id,
    )


def plan_remove_negatives(
    session: Session,
    profile_id: int,
    keyword_ids: Iterable[str] = (),
    target_ids: Iterable[str] = (),
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """Usuniecie wykluczen - keywordow i wykluczonych ASIN-ow.

    Amazon nie pozwala zmienic tekstu ani dopasowania istniejacego wykluczenia,
    wiec "edycja" to zawsze usuniecie starego i dodanie nowego. Usuniecie jest
    archiwizacja i jest **nieodwracalne** - zarchiwizowanego wykluczenia nie da
    sie wznowic. Dlatego opis planu mowi o tym wprost.
    """
    kw = [str(i) for i in keyword_ids]
    tg = [str(i) for i in target_ids]

    teksty = dict(
        session.execute(
            select(NegativeKeyword.keyword_id, NegativeKeyword.keyword_text)
            .where(NegativeKeyword.keyword_id.in_(kw))
        ).all()
    ) if kw else {}
    poziomy = dict(
        session.execute(
            select(NegativeKeyword.keyword_id, NegativeKeyword.level)
            .where(NegativeKeyword.keyword_id.in_(kw))
        ).all()
    ) if kw else {}
    wyrazenia = dict(
        session.execute(
            select(NegativeTarget.target_id, NegativeTarget.resolved_expression)
            .where(NegativeTarget.target_id.in_(tg))
        ).all()
    ) if tg else {}

    planned: list[dict[str, Any]] = []
    for i in kw:
        planned.append({
            "id": i, "entity_type": "NEGATIVE", "entity_name": teksty.get(i) or i,
            "field": "state", "old": "ENABLED", "new": "ARCHIVED",
            "level": poziomy.get(i) or "AD_GROUP", "co": "KEYWORD",
        })
    for i in tg:
        planned.append({
            "id": i, "entity_type": "NEGATIVE_ASIN",
            "entity_name": wyrazenia.get(i) or i,
            "field": "state", "old": "ENABLED", "new": "ARCHIVED",
            "co": "TARGET",
        })

    return _create_plan(
        session, profile_id, "remove_negatives",
        f"Usunięcie {len(planned)} wykluczeń (nieodwracalne)",
        {}, planned, source, rule_id,
    )


# Typy wyrazen, ktore celuja w PRODUKT, a nie w slowo. Lista jest po
# prefiksie, bo Amazon dokłada warianty (ASIN_PRICE_LESS_THAN,
# ASIN_REVIEW_RATING_BETWEEN...), a wszystkie zaczynaja sie tak samo.
_PREFIKSY_PRODUKTOWE = ("ASIN", "CATEGORY", "BRAND")


def _celuje_w_produkty(wyrazenie: Any) -> bool:
    """Czy target celuje w produkty (ASIN/marka/kategoria), a nie w slowa.

    `KEYWORD_GROUP_SAME_AS` i zapytania `QUERY_*` to dalej slowa - grupa, ktora
    ma tylko takie targety, przyjmuje keywordy bez przeszkod.
    """
    if not wyrazenie:
        return False
    if isinstance(wyrazenie, str):
        try:
            wyrazenie = json.loads(wyrazenie)
        except (ValueError, TypeError):
            # Nieparsowalne wyrazenie: patrzymy na goly tekst. Lepiej uznac
            # grupe za produktowa i ja pominac, niz wrzucic tam keyword.
            tekst = wyrazenie.upper()
            return any(p in tekst for p in _PREFIKSY_PRODUKTOWE)
    if isinstance(wyrazenie, dict):
        wyrazenie = [wyrazenie]
    if not isinstance(wyrazenie, (list, tuple)):
        return False
    for czesc in wyrazenie:
        typ = (czesc.get("type") if isinstance(czesc, dict) else "") or ""
        if typ.upper().startswith(_PREFIKSY_PRODUKTOWE):
            return True
    return False


def _grupy_przyjmujace_keywordy(session: Session, profile_id: int) -> list[dict[str, Any]]:
    """Grupy reklam, do ktorych Amazon w ogole przyjmie keyword pozytywny.

    Warunki nie sa uznaniowe - kazdy odpowiada odmowie po stronie Amazona:

    * **kampania MANUAL** - automatyczna nie przyjmuje keywordow pozytywnych
      w ogole, wylacznie wykluczenia,
    * **Sponsored Products** - SB i SD maja wlasne zasoby i wlasne reguly,
    * **grupa ma juz co najmniej jeden keyword** - to jest dowod, ze slowa
      w niej dzialaja,
    * **grupa nie ma targetu PRODUKTOWEGO** - grupa product targeting sluzy
      do celowania w ASIN-y, marki i kategorie, nie w slowa.

    Slowo "produktowy" jest tu istotne, bo nie kazdy target nim jest.
    Wiekszosc grup keywordowych na koncie ma obok slow jeden target
    `KEYWORD_GROUP_SAME_AS` - to targetowanie po grupie slow, czyli dalej
    slowa. Odrzucanie grup "z jakimkolwiek targetem" wywalilo by 12 poprawnych
    grup manualnych i zostawilo panel bez celow. Liczy sie wylacznie
    targetowanie po produkcie: ASIN, marka, kategoria, cena, ocena.

    Ostatni warunek jest bezwzgledny i to jest swiadoma zmiana. Przez chwile
    stalo tu samo "grupa ma keywordy", bo wariant z targetami odrzucal grupy
    keywordowe z jednym zablakanym targetem (SP_Miekka_MK: 4 keywordy i 1
    target) i fraza nie miala dokad trafic. Lekarstwo okazalo sie gorsze od
    choroby: wystarczylo, ze ktos wrzucil recznie kilka slow do grupy
    produktowej, a ta stawala sie pelnoprawnym celem - i wygrywala z wlasciwa
    grupa keywordowa, bo ranking patrzy na pokrycie produktowe, nie na
    charakter grupy. Tak `SP_810-811-LP_PT` (4 keywordy, 2 targety po
    kategorii i marce) przejmowal fraze z `SP_810-811-LP_MK`, czyli dokladnie
    odwrotnie, niz powinno.

    Grupa mieszana nie znika po cichu - wraca w `skipped` z powodem, wiec
    widac, ze istnieje i czemu odpadla. To lepsze niz dosypywanie jej slow
    i odkrywanie tego pozniej po stronie Amazona.
    """
    kandydaci = session.execute(
        select(
            AdGroup.ad_group_id, AdGroup.name, AdGroup.campaign_id,
            Campaign.name, Campaign.portfolio_id,
        )
        .join(Campaign, Campaign.campaign_id == AdGroup.campaign_id)
        .where(
            Campaign.profile_id == profile_id,
            Campaign.targeting_type == "MANUAL",
            Campaign.ad_product == "SP",
            Campaign.state == "ENABLED",
            AdGroup.state == "ENABLED",
        )
    ).all()
    if not kandydaci:
        return []

    ids = [k[0] for k in kandydaci]

    ile_keywordow: dict[str, int] = dict(
        session.execute(
            select(Keyword.ad_group_id, func.count(Keyword.keyword_id))
            .where(Keyword.ad_group_id.in_(ids), Keyword.state == "ENABLED")
            .group_by(Keyword.ad_group_id)
        ).all()
    )
    # Targety produktowe przesadzaja o charakterze grupy, ale trzeba je odroznic
    # od targetowania po grupie slow - to drugie jest dalej targetowaniem na
    # slowa i wystepuje w wiekszosci poprawnych grup keywordowych.
    ile_targetow: dict[str, int] = {}
    for gid, wyrazenie in session.execute(
        select(Target.ad_group_id, Target.resolved_expression)
        .where(Target.ad_group_id.in_(ids), Target.state == "ENABLED")
    ).all():
        if _celuje_w_produkty(wyrazenie):
            ile_targetow[str(gid)] = ile_targetow.get(str(gid), 0) + 1
    produkty: dict[str, set[str]] = {}
    for gid, sku in session.execute(
        select(ProductAd.ad_group_id, ProductAd.sku)
        .where(ProductAd.ad_group_id.in_(ids), ProductAd.state == "ENABLED")
    ).all():
        if sku:
            produkty.setdefault(str(gid), set()).add(sku)

    out = []
    odrzucone: list[dict[str, Any]] = []
    for gid, nazwa_grupy, cid, nazwa_kampanii, portfel in kandydaci:
        gid = str(gid)
        if ile_targetow.get(gid):
            # Grupa celuje w produkty, nie w slowa - keyword jest tu obcym
            # cialem, nawet jesli kilka slow juz w niej lezy.
            odrzucone.append({
                "campaign_name": nazwa_kampanii,
                "ad_group_name": nazwa_grupy,
                "targets": ile_targetow[gid],
                "keywords": ile_keywordow.get(gid, 0),
            })
            continue
        if not ile_keywordow.get(gid):
            # Zero keywordow i zero targetow = grupa pusta. Nie wsadzamy tam
            # slowa na slepo - nie wiadomo, czym ta grupa ma byc.
            continue
        out.append({
            "ad_group_id": gid,
            "ad_group_name": nazwa_grupy,
            "campaign_id": str(cid),
            "campaign_name": nazwa_kampanii,
            "portfolio_id": portfel,
            "keywords": ile_keywordow.get(gid, 0),
            "skus": produkty.get(gid, set()),
        })
    if odrzucone:
        log.info(
            "Harvest: %s grup pominietych jako produktowe (%s)",
            len(odrzucone),
            ", ".join(f"{o['campaign_name']}/{o['ad_group_name']}" for o in odrzucone[:5]),
        )
    return out


# Ile asortymentu grupy docelowej musi pochodzic ze zrodla, zeby uznac ja za
# "te same produkty". Prog jest oceny, nie prawem natury - ponizej niego
# proponujemy raczej nic niz cokolwiek.
MIN_POKRYCIE_CELU = 0.25
MIN_WSPOLNYCH_SKU = 8


def _wybierz_grupe_docelowa(
    kandydaci: list[dict[str, Any]], skus_zrodla: set[str], portfel_zrodla: Any,
) -> dict[str, Any] | None:
    """Grupa manualna reklamujaca te same produkty co zrodlo frazy.

    Zgodnosc produktowa jest warunkiem, nie preferencja. Przejeta fraza ma
    prowadzic na te sama karte produktu, na ktorej zarabiala w automacie -
    wrzucenie jej do grupy z innym asortymentem zmienia sens zmiany i zwykle
    wypada gorzej niz niezrobienie nic.

    Kluczowy jest **kierunek liczenia pokrycia**. Kampania automatyczna miewa
    setki SKU, manualna kilkanascie, wiec liczac od strony zrodla kazde
    dopasowanie wyglada na marginalne: 13 wspolnych SKU z 332 to 4% i brzmi
    jak przypadek. Odwrotnie ta sama para mowi co innego - te 13 SKU stanowi
    81% asortymentu grupy docelowej, czyli manual jest wyostrzonym podzbiorem
    automatu. To jest relacja, ktorej szukamy, wiec pokrycie liczymy od strony
    CELU, a nie zrodla.

    Gdy zadna grupa nie spelnia progu, zwracamy None. Lepiej powiedziec
    "nie ma dokad", niz zaproponowac przypadkowe miejsce.
    """
    if not kandydaci:
        return None

    punktowane = []
    for k in kandydaci:
        wspolne = (skus_zrodla & k["skus"]) if skus_zrodla else set()
        if not wspolne:
            continue
        pokrycie_celu = len(wspolne) / len(k["skus"]) if k["skus"] else 0.0
        if pokrycie_celu < MIN_POKRYCIE_CELU and len(wspolne) < MIN_WSPOLNYCH_SKU:
            continue
        pokrycie_zrodla = len(wspolne) / len(skus_zrodla) if skus_zrodla else 0.0
        punktowane.append((
            round(pokrycie_celu, 3),
            1 if k["portfolio_id"] == portfel_zrodla else 0,
            len(wspolne),
            k["keywords"],
            k,
            pokrycie_zrodla,
        ))

    if not punktowane:
        return None
    # Najpierw jak bardzo cel jest "o tym samym", potem to samo portfolio,
    # dalej liczba wspolnych produktow, na koncu grupa bardziej rozbudowana -
    # czyli taka, ktora juz udowodnila, ze zyje.
    punktowane.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    najlepszy = punktowane[0]
    return {
        **najlepszy[4],
        "wspolne_sku": najlepszy[2],
        "pokrycie_celu": round(najlepszy[0] * 100),
    }


def plan_harvest(
    session: Session,
    profile_id: int,
    items: Iterable[dict[str, Any]],
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """items: [{campaignId, adGroupId, keywordText, matchType?, bid?}]

    `campaignId` i `adGroupId` z wejscia wskazuja **zrodlo** frazy - miejsce,
    w ktorym zostala zauwazona w raporcie. To nie jest miejsce docelowe i nie
    wolno go tak traktowac: fraza pochodzi zwykle z kampanii automatycznej,
    a ta nie przyjmuje keywordow pozytywnych w ogole. Wczesniej plan odsylal
    slowo tam, skad przyszlo, i Amazon odrzucal wieksza czesc pozycji przy
    zatwierdzaniu - co jest najgorszym momentem na dowiedzenie sie o tym.

    Sens przejmowania frazy jest odwrotny: wyprowadzic ja z automatu do
    kampanii manualnej reklamujacej **te same produkty**, gdzie mozna ustawic
    wlasna stawke.

    Pozycje bez sensownego miejsca docelowego nie trafiaja do planu. Wracaja
    w `skipped` z powodem, zeby bylo widac, czego nie da sie zrobic i czemu.
    """
    items = list(items)
    kandydaci = _grupy_przyjmujace_keywordy(session, profile_id)

    # Produkty grup zrodlowych - jednym zapytaniem, nie po jednym na pozycje.
    zrodlowe_grupy = {str(it["adGroupId"]) for it in items if it.get("adGroupId")}
    skus_zrodel: dict[str, set[str]] = {}
    if zrodlowe_grupy:
        for gid, sku in session.execute(
            select(ProductAd.ad_group_id, ProductAd.sku)
            .where(ProductAd.ad_group_id.in_(list(zrodlowe_grupy)))
        ).all():
            if sku:
                skus_zrodel.setdefault(str(gid), set()).add(sku)

    portfele = dict(
        session.execute(
            select(Campaign.campaign_id, Campaign.portfolio_id)
            .where(Campaign.profile_id == profile_id)
        ).all()
    )
    # Nazwy kampanii zrodlowych - zeby w kolejce bylo widac nie tylko dokad
    # fraza idzie, ale i skad. Bez tego wiersz mowi "dodaj keyword do X",
    # co nie tlumaczy, dlaczego akurat teraz i dlaczego akurat tam.
    nazwy_kampanii = dict(
        session.execute(
            select(Campaign.campaign_id, Campaign.name)
            .where(Campaign.profile_id == profile_id)
        ).all()
    )

    # Keywordy juz istniejace - Amazon odrzuca duplikat, a plan wygladalby na
    # wykonalny az do zatwierdzenia.
    istniejace = {
        (str(gid), (tekst or "").strip().lower(), (mt or "").upper())
        for gid, tekst, mt in session.execute(
            select(Keyword.ad_group_id, Keyword.keyword_text, Keyword.match_type)
            .where(Keyword.profile_id == profile_id, Keyword.state != "ARCHIVED")
        ).all()
    }

    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    scalone: dict[tuple, int] = {}   # klucz -> indeks w planned

    for it in items:
        tekst = (it.get("keywordText") or "").strip()
        dopasowanie = (it.get("matchType") or "EXACT").upper()
        if not tekst:
            continue

        powod = _dlaczego_zla_fraza(tekst)
        if powod:
            skipped.append({"keyword": tekst, "reason": powod})
            continue

        zrodlo_grupa = str(it.get("adGroupId") or "")
        cel = _wybierz_grupe_docelowa(
            kandydaci,
            skus_zrodel.get(zrodlo_grupa, set()),
            portfele.get(str(it.get("campaignId"))),
        )
        if cel is None:
            skipped.append({
                "keyword": tekst,
                "reason": (
                    "Brak grupy keywordowej reklamującej te same produkty — "
                    "nie ma dokąd przenieść tej frazy. Grupy product targeting "
                    "się nie liczą: celują w ASIN-y i kategorie, nie w słowa. "
                    "Załóż kampanię ścisłą na ten asortyment albo wskaż grupę "
                    "ręcznie."
                ),
            })
            continue

        klucz = (cel["ad_group_id"], tekst.lower(), dopasowanie)
        if klucz in istniejace:
            skipped.append({
                "keyword": tekst,
                "reason": (
                    f"Ten keyword już istnieje w grupie {cel['ad_group_name']} "
                    f"({cel['campaign_name']})."
                ),
            })
            continue

        stawka = float(it["bid"]) if it.get("bid") is not None else None

        if klucz in scalone:
            # Ta sama fraza z dwoch kampanii zrodlowych. To jedno slowo, nie
            # dwa - zostawiamy wyzsza stawke, bo nizsza czesto nie wystarcza,
            # zeby w ogole wejsc do aukcji na dopasowaniu scislym.
            poz = planned[scalone[klucz]]
            poprzednia = poz["item"].get("bid")
            if stawka is not None and (poprzednia is None or stawka > poprzednia):
                poz["item"]["bid"] = stawka
            poz["zrodla"] = poz.get("zrodla", 1) + 1
            poz["new_label"] = _etykieta_harvest(
                tekst, dopasowanie, poz["item"].get("bid"), cel, poz["zrodla"],
                nazwy_kampanii.get(str(it.get("campaignId"))),
            )
            continue

        docelowy = {
            "campaignId": cel["campaign_id"],
            "adGroupId": cel["ad_group_id"],
            "keywordText": tekst,
            "matchType": dopasowanie,
            **({"bid": stawka} if stawka is not None else {}),
        }
        scalone[klucz] = len(planned)
        planned.append({
            "id": cel["ad_group_id"],
            "entity_type": "KEYWORD",
            "field": "new_keyword",
            "old": None,
            "new": f"{tekst} ({dopasowanie})",
            # Stawka musi byc widoczna w kolejce - bez niej zatwierdza sie
            # dodanie keyworda, nie wiedzac, ile bedzie kosztowal.
            "new_label": _etykieta_harvest(
                tekst, dopasowanie, stawka, cel, 1,
                nazwy_kampanii.get(str(it.get("campaignId"))),
            ),
            "reason": it.get("reason"),
            "zrodla": 1,
            "item": docelowy,
        })

    opis = f"Dodanie {len(planned)} keywordow"
    if skipped:
        opis += f" ({len(skipped)} pominietych)"

    # Gdy nie ma czego dodac, nie zakladamy planu. Pusty wpis w kolejce niczego
    # nie wnosi - nie da sie go zatwierdzic, a wyglada jak zadanie do zrobienia
    # i trzeba go recznie usuwac. Zamiast tego oddajemy same powody, zeby bylo
    # widac, dlaczego sie nie da: to jest tresc, ktorej uzytkownik szuka.
    if not planned:
        return {
            "requires_confirmation": False,
            "plan_id": None,
            "kind": "harvest",
            "summary": (
                f"Nie ma czego dodac - {len(skipped)} pozycji pominietych"
                if skipped else "Nie ma czego dodac"
            ),
            "planned": [],
            "skipped": skipped,
        }

    wynik = _create_plan(
        session, profile_id, "harvest", opis,
        {"skipped": skipped}, planned, source, rule_id,
    )
    # Pominiete doklejamy takze przy planie niepustym - czesc pozycji moze
    # odpasc, a bez tego znikalyby bez sladu w interfejsie.
    return {**wynik, "skipped": skipped}


# Limity Amazona dla keywordText w Sponsored Products. Fraza wyszukiwania
# z raportu bywa czyms, co keywordem byc nie moze - np. calym tytulem
# produktu, ktory klient wkleil w wyszukiwarke (140 znakow, 24 slowa).
# Amazon odrzuca to jako PATTERN_NOT_MATCHED, a odrzucenie jednej pozycji
# w partii oznaczalo dotad jako wadliwa cala partie.
MAX_ZNAKOW_FRAZY = 80
MAX_SLOW_FRAZY = 10
_ZNAKI_ZAKAZANE = set(',;:!?()[]{}"*#@%^|\\<>=~$')


def _dlaczego_zla_fraza(tekst: str) -> str | None:
    """Powod, dla ktorego Amazon odrzuci te fraze jako keyword - albo None."""
    if len(tekst) > MAX_ZNAKOW_FRAZY:
        return (
            f"Fraza ma {len(tekst)} znaków, Amazon dopuszcza {MAX_ZNAKOW_FRAZY} — "
            "to raczej wklejony tytuł produktu niż słowo kluczowe."
        )
    slow = len(tekst.split())
    if slow > MAX_SLOW_FRAZY:
        return f"Fraza ma {slow} słów, Amazon dopuszcza {MAX_SLOW_FRAZY}."
    zle = sorted(set(tekst) & _ZNAKI_ZAKAZANE)
    if zle:
        return f"Fraza zawiera znaki, których Amazon nie przyjmuje w keywordzie: {' '.join(zle)}"
    return None


def _etykieta_harvest(
    tekst: str, dopasowanie: str, stawka: float | None,
    cel: dict[str, Any], zrodla: int, zrodlo_nazwa: str | None = None,
) -> str:
    czesci = [tekst, dopasowanie.lower()]
    czesci.append(
        f"stawka {round(float(stawka), 2)}" if stawka is not None
        else "stawka domyślna grupy"
    )
    # Kampanie docelowa niesie juz sam wiersz (to jego obiekt), wiec tutaj
    # dopisujemy to, czego w nim nie ma: skad fraza pochodzi. Dopiero para
    # "z automatu -> do manuala" tlumaczy sens przejecia.
    if zrodlo_nazwa:
        czesci.append(f"z {zrodlo_nazwa} → do {cel['campaign_name']}")
    else:
        czesci.append(f"do {cel['campaign_name']} / {cel['ad_group_name']}")
    # Jakosc dopasowania jest czescia decyzji, nie ciekawostka - przy 30%
    # warto rzucic okiem, przy 90% mozna zatwierdzac bez zastanowienia.
    if cel.get("wspolne_sku"):
        czesci.append(
            f"wspólnych produktów {cel['wspolne_sku']} ({cel['pokrycie_celu']}% grupy)"
        )
    if zrodla > 1:
        czesci.append(f"scalone z {zrodla} kampanii")
    return " · ".join(czesci)


PLACEMENT_LABEL = {
    "PLACEMENT_TOP": "Góra wyników",
    "PLACEMENT_REST_OF_SEARCH": "Reszta wyników",
    "PLACEMENT_PRODUCT_PAGE": "Karta produktu",
}


def plan_placements(
    session: Session,
    profile_id: int,
    changes: Iterable[dict[str, Any]],
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """changes: [{campaign_id, strategy, placements: [{placement, old, new}]}]

    Amazon nadpisuje caly zestaw placementBidding, wiec payload musi zawierac
    wszystkie placementy kampanii - takze te niezmienione.
    """
    changes = list(changes)
    planned: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for ch in changes:
        cid = str(ch["campaign_id"])
        moved = [p for p in ch["placements"] if round(p.get("old") or 0) != round(p["new"])]
        if not moved:
            continue
        for p in moved:
            planned.append({
                "id": cid,
                "entity_type": "CAMPAIGN",
                "field": f"placement:{PLACEMENT_LABEL.get(p['placement'], p['placement'])}",
                "old": f"{round(p.get('old') or 0)}%",
                "new": f"{round(p['new'])}%",
                # Surowa nazwa potrzebna przy zatwierdzaniu czesci pozycji -
                # etykieta po polsku nie wystarczy do odtworzenia zestawu
                "placement": p["placement"],
                "old_value": float(p.get("old") or 0),
                "new_value": float(p["new"]),
            })
        items.append({
            "campaignId": cid,
            "strategy": ch.get("strategy") or "LEGACY_FOR_SALES",
            "placements": [
                {"placement": p["placement"], "percentage": p["new"]} for p in ch["placements"]
            ],
        })

    return _create_plan(
        session, profile_id, "placements",
        f"Modyfikatory placementow w {len(items)} kampaniach",
        {"items": items}, planned, source, rule_id,
    )


def plan_product_ads(
    session: Session,
    profile_id: int,
    ad_ids: Iterable[str],
    state: str,
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """Wstrzymanie lub wznowienie reklam produktowych."""
    ids = [str(a) for a in ad_ids]
    state = state.upper()
    old = dict(
        session.execute(
            select(ProductAd.ad_id, ProductAd.state).where(ProductAd.ad_id.in_(ids))
        ).all()
    )
    planned = [
        {"id": i, "entity_type": "PRODUCT_AD", "field": "state",
         "old": old.get(i), "new": state}
        for i in ids
        if old.get(i) != state
    ]
    label = "Wstrzymanie" if state == "PAUSED" else "Wznowienie"
    return _create_plan(
        session, profile_id, "product_ads",
        f"{label} {len(planned)} reklam produktowych",
        {"state": state}, planned, source, rule_id,
    )


# --------------------------------------------------------------------------- #
# Tworzenie kampanii
# --------------------------------------------------------------------------- #

MIN_BUDGET = 1.0
MAX_BUDGET = 10_000.0

# Grupy targetowania automatycznego, ktore Amazon przyjmuje przy tworzeniu.
AUTO_GROUPS = {
    "QUERY_HIGH_REL_MATCHES", "QUERY_BROAD_REL_MATCHES",
    "ASIN_SUBSTITUTE_RELATED", "ASIN_ACCESSORY_RELATED",
}

BIDDING_STRATEGIES = {"LEGACY_FOR_SALES", "AUTO_FOR_SALES", "MANUAL"}
MATCH_TYPES = {"EXACT", "PHRASE", "BROAD"}
NEGATIVE_MATCH_TYPES = {"NEGATIVE_EXACT", "NEGATIVE_PHRASE"}


class SpecError(ValueError):
    """Blad w opisie kampanii - do pokazania uzytkownikowi, nie do logow."""


def _validate_campaign_spec(
    session: Session, profile_id: int, spec: dict[str, Any]
) -> dict[str, Any]:
    """Sprawdza opis kampanii, zanim cokolwiek trafi do kolejki.

    Walidujemy tutaj, a nie przy zatwierdzaniu, bo blad wylapany po utworzeniu
    kampanii zostawia na koncie polprodukt, ktorego nie da sie cofnac jednym
    ruchem. Lepiej odrzucic wczesnie i glosno.
    """
    name = (spec.get("name") or "").strip()
    if not name:
        raise SpecError("Kampania musi mieć nazwę.")
    if len(name) > 128:
        raise SpecError("Nazwa kampanii nie może przekraczać 128 znaków.")

    istnieje = session.execute(
        select(Campaign.campaign_id).where(
            Campaign.profile_id == profile_id,
            Campaign.name == name,
            Campaign.state != "ARCHIVED",
        )
    ).first()
    if istnieje:
        raise SpecError(
            f"Kampania o nazwie {name} już istnieje na tym rynku. Amazon dopuszcza "
            "duplikaty nazw, ale wtedy nie da się ich rozróżnić w raportach — "
            "wybierz inną nazwę."
        )

    try:
        budget = round(float(spec.get("daily_budget")), 2)
    except (TypeError, ValueError):
        raise SpecError("Budżet dzienny musi być liczbą.") from None
    if not MIN_BUDGET <= budget <= MAX_BUDGET:
        raise SpecError(f"Budżet dzienny musi mieścić się w {MIN_BUDGET}–{MAX_BUDGET}.")

    targeting = (spec.get("targeting_type") or "").upper()
    if targeting not in {"AUTO", "MANUAL"}:
        raise SpecError("Rodzaj targetowania to AUTO albo MANUAL.")

    strategy = (spec.get("bidding_strategy") or "LEGACY_FOR_SALES").upper()
    if strategy not in BIDDING_STRATEGIES:
        raise SpecError(f"Nieznana strategia stawek: {strategy}.")

    grupa = spec.get("ad_group") or {}
    grupa_nazwa = (grupa.get("name") or "").strip()
    if not grupa_nazwa:
        raise SpecError("Grupa reklam musi mieć nazwę.")
    try:
        default_bid = round(float(grupa.get("default_bid")), 2)
    except (TypeError, ValueError):
        raise SpecError("Domyślna stawka grupy musi być liczbą.") from None
    if not MIN_BID <= default_bid <= MAX_BID:
        raise SpecError(f"Domyślna stawka musi mieścić się w {MIN_BID}–{MAX_BID}.")

    skus = [s.strip() for s in (spec.get("skus") or []) if s and s.strip()]
    asins = [a.strip().upper() for a in (spec.get("asins") or []) if a and a.strip()]
    if not skus and not asins:
        raise SpecError("Kampania bez produktu nic nie wyświetli — dodaj SKU albo ASIN.")

    keywords: list[dict[str, Any]] = []
    product_targets: list[dict[str, Any]] = []
    auto_groups: list[str] = []

    if targeting == "MANUAL":
        for k in spec.get("keywords") or []:
            tekst = (k.get("text") or "").strip()
            if not tekst:
                continue
            match = (k.get("match_type") or "EXACT").upper()
            if match not in MATCH_TYPES:
                raise SpecError(f"Nieznane dopasowanie: {match}.")
            bid = k.get("bid")
            keywords.append({
                "text": tekst,
                "match_type": match,
                "bid": round(float(bid), 2) if bid is not None else None,
            })

        for t in spec.get("product_targets") or []:
            asin = (t.get("asin") or "").strip().upper()
            if not asin:
                continue
            bid = t.get("bid")
            product_targets.append({
                "expression": [{"type": "ASIN_SAME_AS", "value": asin}],
                "bid": round(float(bid), 2) if bid is not None else None,
                "label": asin,
            })

        if not keywords and not product_targets:
            raise SpecError(
                "Kampania ręczna bez słów kluczowych i bez targetowania produktowego "
                "nie ma na co licytować — dodaj przynajmniej jedno."
            )
    else:
        auto_groups = [g.upper() for g in (spec.get("auto_groups") or sorted(AUTO_GROUPS))]
        nieznane = set(auto_groups) - AUTO_GROUPS
        if nieznane:
            raise SpecError(f"Nieznane grupy automatyczne: {', '.join(sorted(nieznane))}.")
        if spec.get("keywords"):
            raise SpecError(
                "Kampania automatyczna nie przyjmuje słów kluczowych — "
                "o dopasowaniu decyduje Amazon."
            )

    negatives: list[dict[str, Any]] = []
    for n in spec.get("negative_keywords") or []:
        tekst = (n.get("text") or "").strip()
        if not tekst:
            continue
        match = (n.get("match_type") or "NEGATIVE_EXACT").upper()
        if match not in NEGATIVE_MATCH_TYPES:
            raise SpecError(f"Nieznane dopasowanie wykluczenia: {match}.")
        negatives.append({"text": tekst, "match_type": match})

    stan = (spec.get("state") or "PAUSED").upper()
    if stan not in {"ENABLED", "PAUSED"}:
        raise SpecError("Kampania może powstać jako aktywna albo wstrzymana.")

    return {
        "name": name,
        "daily_budget": budget,
        "targeting_type": targeting,
        "bidding_strategy": strategy,
        "start_date": spec.get("start_date"),
        "end_date": spec.get("end_date"),
        "portfolio_id": str(spec["portfolio_id"]) if spec.get("portfolio_id") else None,
        "final_state": stan,
        "ad_group": {"name": grupa_nazwa, "default_bid": default_bid},
        "skus": skus,
        "asins": asins,
        "keywords": keywords,
        "product_targets": product_targets,
        "auto_groups": auto_groups,
        "negative_keywords": negatives,
    }


def plan_create_campaign(
    session: Session,
    profile_id: int,
    spec: dict[str, Any],
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """Buduje plan utworzenia kampanii wraz z cala jej zawartoscia.

    Plan jest niepodzielny: nie da sie zatwierdzic grupy reklam bez kampanii,
    w ktorej ma powstac, ani slowa kluczowego bez grupy. Dlatego przy
    zatwierdzaniu wybor pojedynczych pozycji jest tu ignorowany.
    """
    czysty = _validate_campaign_spec(session, profile_id, spec)

    portfolio_name = None
    if czysty["portfolio_id"]:
        # W planie trzymamy identyfikator jako tekst, bo API Amazona oczekuje
        # stringa. W bazie kolumna jest liczbowa - SQLite pogodzilby jedno
        # z drugim po cichu, Postgres nie, wiec rzutujemy jawnie.
        try:
            portfolio_name = session.execute(
                select(Portfolio.name).where(
                    Portfolio.portfolio_id == int(czysty["portfolio_id"])
                )
            ).scalar()
        except (TypeError, ValueError):
            portfolio_name = None

    tryb = "automatyczna" if czysty["targeting_type"] == "AUTO" else "ręczna"
    planned: list[dict[str, Any]] = [
        {
            "id": "nowa-kampania",
            "entity_type": "CAMPAIGN",
            "entity_name": czysty["name"],
            "field": "campaign",
            "old": None,
            "new": czysty["name"],
            "new_label": (
                f"{tryb}, budżet {czysty['daily_budget']}/dzień"
                + (f", portfolio {portfolio_name}" if portfolio_name else "")
            ),
        },
        {
            "id": "nowa-grupa",
            "entity_type": "AD_GROUP",
            "entity_name": czysty["ad_group"]["name"],
            "field": "ad_group",
            "old": None,
            "new": czysty["ad_group"]["name"],
            "new_label": f"stawka domyślna {czysty['ad_group']['default_bid']}",
        },
    ]

    for sku in czysty["skus"]:
        planned.append({
            "id": sku, "entity_type": "PRODUCT_AD", "entity_name": sku,
            "field": "product_ad", "old": None, "new": sku, "new_label": "SKU",
        })
    for asin in czysty["asins"]:
        planned.append({
            "id": asin, "entity_type": "PRODUCT_AD", "entity_name": asin,
            "field": "product_ad", "old": None, "new": asin, "new_label": "ASIN",
        })
    for k in czysty["keywords"]:
        planned.append({
            "id": k["text"], "entity_type": "KEYWORD", "entity_name": k["text"],
            "field": "new_keyword", "old": None, "new": k["text"],
            "new_label": k["match_type"].lower()
            + (f", stawka {k['bid']}" if k["bid"] is not None else " (stawka grupy)"),
        })
    for t in czysty["product_targets"]:
        planned.append({
            "id": t["label"], "entity_type": "TARGET", "entity_name": t["label"],
            "field": "new_target", "old": None, "new": t["label"],
            "new_label": "produkt" + (f", stawka {t['bid']}" if t["bid"] is not None else ""),
        })
    for g in czysty["auto_groups"]:
        planned.append({
            "id": g, "entity_type": "TARGET",
            "entity_name": targeting_names.AUTO_LABELS.get(g, g),
            "field": "new_target", "old": None, "new": g,
            "new_label": "grupa automatyczna",
        })
    for n in czysty["negative_keywords"]:
        planned.append({
            "id": n["text"], "entity_type": "NEGATIVE", "entity_name": n["text"],
            "field": "keywordText", "old": None, "new": n["text"],
            "new_label": "wykluczenie " + n["match_type"].replace("NEGATIVE_", "").lower(),
        })

    stan_opis = "aktywna" if czysty["final_state"] == "ENABLED" else "wstrzymana"
    return _create_plan(
        session, profile_id, "create_campaign",
        f"Nowa kampania {czysty['name']} ({tryb}, {stan_opis}) — {len(planned)} elementów",
        {"spec": czysty}, planned, source, rule_id,
    )


def plan_negative_targets(
    session: Session,
    profile_id: int,
    items: Iterable[dict[str, Any]],
    *,
    source: str = "MANUAL",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """items: [{campaignId, adGroupId, asin}] albo [{campaignId, adGroupId, brandId, brandName?}]

    Marka wyklucza wszystkie produkty danego producenta naraz - jedna pozycja
    zamiast kilkudziesieciu ASIN-ow. Amazon chce liczbowego `brandId`, wiec
    nazwe wozimy tylko po to, zeby kolejka zatwierdzen nie pokazywala golego
    numeru.
    """
    items = [dict(it) for it in items]
    planned = []
    for it in items:
        marka = bool(it.get("brandId"))
        nazwa = it.pop("brandName", None)
        # Wyrazenie liczymy raz, tutaj - plan niesie je dalej, wiec wykonawca
        # i lokalna kopia encji widza dokladnie to, co poszlo do Amazona.
        it["expression"] = entities.wyrazenie_negatywu(it)
        planned.append({
            "id": it["adGroupId"],
            "entity_type": "NEGATIVE_BRAND" if marka else "NEGATIVE_ASIN",
            "field": "brand" if marka else "asin",
            "old": None,
            "new": (
                f"marka {nazwa or it['brandId']}" if marka
                else str(it["asin"]).upper()
            ),
            "item": it,
        })

    marek = sum(1 for p in planned if p["entity_type"] == "NEGATIVE_BRAND")
    asinow = len(planned) - marek
    czesci = []
    if asinow:
        czesci.append(f"{asinow} ASIN-ow")
    if marek:
        czesci.append(f"{marek} marek")
    return _create_plan(
        session, profile_id, "negative_targets",
        "Wykluczenie " + " i ".join(czesci or ["0 pozycji"]),
        {}, planned, source, rule_id,
    )

# --------------------------------------------------------------------------- #
# Krok 2: wykonanie (prywatne - wylacznie przez apply_plan)
# --------------------------------------------------------------------------- #


def _finalize(
    session: Session,
    profile_id: int,
    planned: list[dict[str, Any]],
    ok_ids: set[str],
    errors: dict[str, str],
    source: str,
    rule_id: int | None,
    apply_local: Any = None,
) -> dict[str, Any]:
    """Zapisuje wynik w audycie i aktualizuje lokalna kopie stanu."""
    applied = failed = 0
    for p in planned:
        # Puste ok_ids przy braku bledow = API nie zwrocilo rozbicia, uznajemy sukces
        success = p["id"] in ok_ids or (not errors and not ok_ids)
        applied += success
        failed += not success
        _log_change(
            session, profile_id, p.get("entity_type", "?"), p["id"], p.get("field", "?"),
            p.get("old"), p.get("new"), source,
            "APPLIED" if success else "FAILED", rule_id, errors.get(p["id"]),
        )
        if success and apply_local:
            apply_local(p)

    return {"applied": applied, "failed": failed, "errors": errors, "planned": planned}


def _execute_bids(session, profile_id, payload, planned, source, rule_id):
    """Zmiana stawek. Kazdy typ produktu ma wlasny zasob w Ads API.

    Wyslanie identyfikatora targetu SD na `/sp/targets` konczy sie
    odrzuceniem - Amazon nie zna go w Sponsored Products. Dlatego najpierw
    ustalamy, do ktorego produktu nalezy kazda pozycja, i dopiero potem
    wybieramy endpoint.
    """
    from ..ads import sb_entities, sd_entities

    ids = [str(p["id"]) for p in planned]
    keywordy = {
        str(kid): (prod, cid, gid)
        for kid, prod, cid, gid in session.execute(
            select(Keyword.keyword_id, Keyword.ad_product,
                   Keyword.campaign_id, Keyword.ad_group_id)
            .where(Keyword.keyword_id.in_(ids))
        ).all()
    }
    produkt_kw = {kid: prod for kid, (prod, _, _) in keywordy.items()}
    produkt_tg = dict(session.execute(
        select(Target.target_id, Target.ad_product).where(Target.target_id.in_(ids))
    ).all())

    def rozpoznaj(p) -> tuple[str, str]:
        """Zwraca (produkt, rodzaj) ustalone z bazy, nie z zapisu w planie.

        Plany sprzed poprawki klasyfikacji maja target SB/SD podpisany jako
        KEYWORD. Gdybysmy im uwierzyli, stawka poszlaby na `/sp/keywords`
        i Amazon by ja odrzucil. Identyfikator jest jednoznaczny, wiec
        pytamy o niego obie tabele.
        """
        tid = str(p["id"])
        if tid in produkt_tg:
            return produkt_tg[tid] or "SP", "TARGET"
        if tid in produkt_kw:
            return produkt_kw[tid] or "SP", "KEYWORD"
        return "SP", p["kind"]

    ok_ids: set[str] = set()
    errors: dict[str, str] = {}

    with AdsClient(profile_id=profile_id) as client:
        grupy: dict[tuple[str, str], list[tuple[str, float]]] = {}
        for p in planned:
            grupy.setdefault(rozpoznaj(p), []).append((p["id"], p["new"]))

        for (prod, kind), pary in grupy.items():
            if prod == "SP" and kind == "KEYWORD":
                wynik, klucz = entities.set_keyword_bids(client, pary), "keywordId"
            elif prod == "SP":
                wynik, klucz = entities.set_target_bids(client, pary), "targetId"
            elif prod == "SB" and kind == "KEYWORD":
                # Stary interfejs SB chce przy stawce takze kampanie i grupe
                rekordy = [
                    {"keywordId": kid, "bid": bid,
                     "campaignId": keywordy[str(kid)][1],
                     "adGroupId": keywordy[str(kid)][2]}
                    for kid, bid in pary
                ]
                wynik, klucz = sb_entities.set_keyword_bids(client, rekordy), "keywordId"
            elif prod == "SD" and kind == "TARGET":
                wynik, klucz = sd_entities.set_target_bids(client, pary), "targetId"
            else:
                # Nie zgadujemy endpointu - lepiej powiedziec wprost, ze tego
                # polaczenia jeszcze nie obslugujemy, niz wyslac w zle miejsce.
                for tid, _ in pary:
                    errors[str(tid)] = (
                        f"Zmiana stawki dla {prod} ({kind}) nie jest jeszcze obslugiwana"
                    )
                continue
            try:
                ok, err = _split_results(wynik, klucz)
            except Exception as exc:  # noqa: BLE001
                # Nieoczekiwany ksztalt odpowiedzi nie moze wywrocic calego
                # planu - reszta pozycji ma isc dalej, a ta grupa dostaje
                # czytelny powod zamiast bledu 500 i "Failed to fetch".
                log.exception("Nieznany ksztalt odpowiedzi dla %s/%s", prod, kind)
                for tid, _ in pary:
                    errors[str(tid)] = f"Nieoczekiwana odpowiedz Amazona: {str(exc)[:120]}"
                continue
            ok_ids |= ok
            errors |= err

    def local(p):
        # Ta sama zasada co wyzej - rodzaj bierzemy z bazy, nie z planu.
        _, kind = rozpoznaj(p)
        obj = session.get(Keyword if kind == "KEYWORD" else Target, p["id"])
        if obj:
            obj.bid = p["new"]

    return _finalize(session, profile_id, planned, ok_ids, errors, source, rule_id, local)


def _po_typie(session, model, ids: list[str]) -> dict[str, tuple[str, str | None, str | None]]:
    """id -> (ad_product, campaign_id, ad_group_id) z bazy, nie z planu.

    Typ kampanii decyduje o zasobie w Ads API, a stary interfejs SB wymaga
    przy keywordzie i targecie takze kampanii i grupy - stad cala trojka.
    """
    kol_grupa = getattr(model, "ad_group_id", None)
    kol_kamp = getattr(model, "campaign_id", None)
    klucz = list(model.__table__.primary_key.columns)[0]
    stmt = select(
        klucz, model.ad_product,
        kol_kamp if kol_kamp is not None else klucz,
        kol_grupa if kol_grupa is not None else klucz,
    ).where(klucz.in_(ids))
    return {
        str(i): (prod or "SP", str(c) if c is not None else None, str(g) if g is not None else None)
        for i, prod, c, g in session.execute(stmt).all()
    }


def _execute_state(session, profile_id, payload, planned, source, rule_id):
    """Zmiana stanu. Kazdy typ obiektu i kazdy typ kampanii ma wlasny zasob.

    Identyfikator targetu SD wyslany na `/sp/targets` konczy sie odrzuceniem -
    Amazon nie zna go w Sponsored Products. Dlatego grupujemy pozycje po
    typie kampanii z bazy i kazda grupe wysylamy tam, gdzie nalezy.
    """
    from ..ads import sb_entities, sd_entities

    entity_type = payload["entity_type"]
    state = payload["state"]
    ids = [str(p["id"]) for p in planned]

    MODELE = {"KEYWORD": Keyword, "TARGET": Target, "CAMPAIGN": Campaign, "AD_GROUP": AdGroup}
    if entity_type not in MODELE:
        raise ValueError(f"Nieobslugiwany typ obiektu przy zmianie statusu: {entity_type}")
    model = MODELE[entity_type]
    info = _po_typie(session, model, ids)
    KLUCZ = {"KEYWORD": "keywordId", "TARGET": "targetId",
             "CAMPAIGN": "campaignId", "AD_GROUP": "adGroupId"}[entity_type]

    grupy: dict[str, list[str]] = {}
    for i in ids:
        grupy.setdefault(info.get(i, ("SP", None, None))[0], []).append(i)

    ok_ids: set[str] = set()
    errors: dict[str, str] = {}
    with AdsClient(profile_id=profile_id) as client:
        for prod, czesc in grupy.items():
            try:
                if prod == "SP":
                    wywolaj = {
                        "KEYWORD": entities.set_keyword_state,
                        "TARGET": entities.set_target_state,
                        "CAMPAIGN": entities.set_campaign_state,
                        "AD_GROUP": entities.set_ad_group_state,
                    }[entity_type]
                    wynik = wywolaj(client, czesc, state)
                elif prod == "SD":
                    wywolaj = {
                        "TARGET": sd_entities.set_target_state,
                        "CAMPAIGN": sd_entities.set_campaign_state,
                        "AD_GROUP": sd_entities.set_ad_group_state,
                    }.get(entity_type)
                    if wywolaj is None:
                        raise NotImplementedError(f"Sponsored Display nie ma obiektu {entity_type}")
                    wynik = wywolaj(client, czesc, state)
                elif prod == "SB":
                    if entity_type == "CAMPAIGN":
                        wynik = sb_entities.set_campaign_state(client, czesc, state)
                    elif entity_type == "AD_GROUP":
                        wynik = sb_entities.set_ad_group_state(client, czesc, state)
                    else:
                        rekordy = [
                            {KLUCZ: i, "campaignId": info[i][1], "adGroupId": info[i][2]}
                            for i in czesc
                        ]
                        wywolaj = (sb_entities.set_keyword_state if entity_type == "KEYWORD"
                                   else sb_entities.set_target_state)
                        wynik = wywolaj(client, rekordy, state)
                else:
                    raise NotImplementedError(f"Nieznany typ kampanii: {prod}")
            except NotImplementedError as exc:
                for i in czesc:
                    errors[i] = str(exc)
                continue
            ok, err = _split_results(wynik, KLUCZ)
            ok_ids |= ok
            # Blad bez identyfikatora (stary interfejs) - przypisz po indeksie
            for k, opis in err.items():
                errors[czesc[int(k)] if k.isdigit() and int(k) < len(czesc) and k not in czesc else k] = opis

    def local(p):
        obj = session.get(model, p["id"])
        if obj:
            obj.state = state

    return _finalize(session, profile_id, planned, ok_ids, errors, source, rule_id, local)


def _execute_budgets(session, profile_id, payload, planned, source, rule_id):
    """Budzety kampanii - SP, SB i SD maja osobne zasoby."""
    from ..ads import sb_entities, sd_entities

    info = _po_typie(session, Campaign, [str(p["id"]) for p in planned])
    grupy: dict[str, list[tuple[str, float]]] = {}
    for p in planned:
        grupy.setdefault(info.get(str(p["id"]), ("SP",))[0], []).append((str(p["id"]), p["new"]))

    ok_ids: set[str] = set()
    errors: dict[str, str] = {}
    with AdsClient(profile_id=profile_id) as client:
        for prod, pary in grupy.items():
            wywolaj = {
                "SP": entities.set_campaign_budgets,
                "SB": sb_entities.set_campaign_budgets,
                "SD": sd_entities.set_campaign_budgets,
            }.get(prod)
            if wywolaj is None:
                for cid, _ in pary:
                    errors[cid] = f"Zmiana budzetu dla {prod} nie jest obslugiwana"
                continue
            ok, err = _split_results(wywolaj(client, pary), "campaignId")
            ok_ids |= ok
            errors |= err

    def local(p):
        obj = session.get(Campaign, p["id"])
        if obj:
            obj.budget_amount = p["new"]

    return _finalize(session, profile_id, planned, ok_ids, errors, source, rule_id, local)


def _sparuj_z_odpowiedzia(
    items: list[dict[str, Any]], result: dict[str, Any], id_field: str,
) -> list[tuple[dict[str, Any], str]]:
    """Laczy przyjete pozycje z identyfikatorami, ktore nadal im Amazon.

    Potrzebne, zeby dopisac nowa encje do lokalnej bazy od razu po
    zatwierdzeniu. Bez tego panel dowiaduje sie o wykluczeniu dopiero przy
    najblizszej synchronizacji encji i do tego czasu proponuje je ponownie.

    Amazon zwykle podaje `index` wskazujacy pozycje w wyslanej partii. Gdy go
    nie ma, przypisujemy po kolejnosci - ale **tylko przy pelnym powodzeniu**.
    Przy czesciowym nie da sie powiedziec, ktora pozycja odpadla, a blednie
    przypisany identyfikator bylby gorszy od jego braku: panel uznalby za
    wykluczona fraze, ktora nadal zbiera klikniecia.
    """
    sukcesy = result.get("success") or []
    pary: list[tuple[dict[str, Any], str]] = []
    bez_indeksu: list[str] = []

    for wpis in sukcesy:
        if not isinstance(wpis, dict):
            continue
        nowy = wpis.get(id_field)
        idx = wpis.get("index")
        if isinstance(idx, int) and 0 <= idx < len(items) and nowy:
            pary.append((items[idx], str(nowy)))
        elif nowy:
            bez_indeksu.append(str(nowy))

    if bez_indeksu and not pary and len(bez_indeksu) == len(items):
        pary = list(zip(items, bez_indeksu))
    elif bez_indeksu:
        log.info(
            "Odpowiedz bez indeksow przy czesciowym powodzeniu (%s z %s) - "
            "nie dopisuje encji lokalnie, doczytaja sie przy synchronizacji",
            len(sukcesy), len(items),
        )
    return pary


def _execute_negatives(session, profile_id, payload, planned, source, rule_id):
    """Wykluczenia fraz. Kazdy typ kampanii ma wlasny zasob w Ads API.

    Negatyw dla kampanii Sponsored Brands wyslany na `/sp/negativeKeywords`
    wraca z "parent program type must be Sponsored Products" - a poniewaz
    Amazon odpowiada na cala partie, jeden taki wpis oznaczal jako wadliwe
    takze poprawne pozycje SP. Dlatego pozycje rozdzielamy po typie kampanii
    (z bazy, nie z planu) i wysylamy kazda grupe osobno.
    """
    from ..ads import sb_entities

    items = [p["item"] for p in planned]
    poziom = payload.get("level", "AD_GROUP")

    kampanie = {str(c) for c in {it.get("campaignId") for it in items} if c}
    produkt_kampanii = dict(session.execute(
        select(Campaign.campaign_id, Campaign.ad_product)
        .where(Campaign.campaign_id.in_(kampanie))
    ).all()) if kampanie else {}

    grupy: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        grupy.setdefault(produkt_kampanii.get(str(it.get("campaignId"))) or "SP", []).append(i)

    applied = 0
    bledy_wszystkie: list[Any] = []
    # Status i opis bledu per pozycja - nie per cala partia jak dotad
    status_poz: dict[int, tuple[str, str | None]] = {}

    with AdsClient(profile_id=profile_id) as client:
        for prod, idx in grupy.items():
            czesc = [items[i] for i in idx]
            if prod == "SP":
                result = entities.add_negative_keywords(client, czesc, level=poziom)
            elif prod == "SB" and poziom.upper() == "AD_GROUP":
                result = sb_entities.add_negative_keywords(client, czesc)
            else:
                opis = (
                    "Sponsored Brands nie ma wykluczen na poziomie kampanii - wyklucz w grupie"
                    if prod == "SB" else
                    f"Wykluczenia fraz dla {prod} nie sa jeszcze obslugiwane"
                )
                for i in idx:
                    status_poz[i] = ("FAILED", opis)
                bledy_wszystkie.append({"message": opis})
                continue

            # Lokalna kopia stanu - zeby panel od razu wiedzial, ze fraza jest
            # wykluczona, i nie proponowal tego drugi raz.
            for item, nowy_id in _sparuj_z_odpowiedzia(czesc, result, "keywordId"):
                if session.get(NegativeKeyword, nowy_id):
                    continue
                session.add(NegativeKeyword(
                    keyword_id=nowy_id,
                    ad_group_id=str(item["adGroupId"]) if item.get("adGroupId") else None,
                    campaign_id=str(item.get("campaignId") or ""),
                    profile_id=profile_id,
                    level=poziom,
                    keyword_text=item.get("keywordText"),
                    match_type=item.get("matchType"),
                    state="ENABLED",
                ))

            sukcesy = result.get("success", [])
            bledy = result.get("error", [])
            applied += len(sukcesy)
            bledy_wszystkie.extend(bledy)
            # Gdy Amazon podaje indeksy, wiemy dokladnie, ktora pozycja odpadla;
            # bez nich cala grupa dostaje wspolny status z pierwszym bledem.
            zle_idx = {b.get("index") for b in bledy if isinstance(b, dict)}
            opis = _first_error(result, "Amazon odrzucił wykluczenie") if bledy else None
            for k, i in enumerate(idx):
                if not bledy:
                    status_poz[i] = ("APPLIED", None)
                elif None in zle_idx or len(zle_idx) != len(bledy):
                    status_poz[i] = ("PARTIAL", opis)
                else:
                    status_poz[i] = ("FAILED", opis) if k in zle_idx else ("APPLIED", None)

    for i, p in enumerate(planned):
        st, opis = status_poz.get(i, ("PARTIAL", None))
        _log_change(
            session, profile_id, "NEGATIVE", p["id"], "keywordText", None, p["new"],
            source, st, rule_id, opis,
        )
    return {
        "applied": applied,
        "failed": len(bledy_wszystkie),
        "errors": [str(b)[:300] for b in bledy_wszystkie[:5]],
        "planned": planned,
    }


def _execute_harvest(session, profile_id, payload, planned, source, rule_id):
    items = [p["item"] for p in planned]
    with AdsClient(profile_id=profile_id) as client:
        result = entities.add_keywords(client, items)

    # Ten sam powod co przy wykluczeniach: bez dopisania keyworda lokalnie
    # panel proponowalby przejecie tej samej frazy przy kazdym odswiezeniu,
    # az do najblizszej synchronizacji encji.
    for item, nowy_id in _sparuj_z_odpowiedzia(items, result, "keywordId"):
        if session.get(Keyword, nowy_id):
            continue
        session.add(Keyword(
            keyword_id=nowy_id,
            ad_group_id=str(item.get("adGroupId") or ""),
            campaign_id=str(item.get("campaignId") or ""),
            profile_id=profile_id,
            ad_product="SP",
            keyword_text=item.get("keywordText"),
            match_type=item.get("matchType", "EXACT"),
            state="ENABLED",
            bid=item.get("bid"),
        ))

    bledy = result.get("error", [])
    applied = len(result.get("success", []))
    failed = len(bledy)
    # Amazon podaje `index` odrzuconej pozycji - wtedy tylko ona dostaje
    # FAILED z powodem, a reszta APPLIED. Bez indeksow nie zgadujemy: cala
    # partia jest PARTIAL, tak jak dotad.
    zle_idx = {b.get("index") for b in bledy if isinstance(b, dict)}
    dokladnie = bledy and None not in zle_idx and len(zle_idx) == len(bledy)
    opis_dla = {}
    if dokladnie:
        for b in bledy:
            opis_dla[b["index"]] = _first_error({"error": [b]}, "Amazon odrzucił keyword")
    for i, p in enumerate(planned):
        if not bledy:
            st, opis = "APPLIED", None
        elif dokladnie:
            st, opis = ("FAILED", opis_dla[i]) if i in zle_idx else ("APPLIED", None)
        else:
            st, opis = "PARTIAL", _first_error(result, "Amazon odrzucił keyword")
        _log_change(
            session, profile_id, "KEYWORD", p["id"], "new_keyword", None, p["new"],
            source, st, rule_id, opis,
        )
    return {"applied": applied, "failed": failed, "errors": bledy, "planned": planned}


def _execute_placements(session, profile_id, payload, planned, source, rule_id):
    items = payload.get("items", [])
    with AdsClient(profile_id=profile_id) as client:
        ok_ids, errors = _split_results(
            entities.set_placement_modifiers(client, items), "campaignId"
        )

    def local(p):
        obj = session.get(Campaign, p["id"])
        if obj:
            match = next((i for i in items if i["campaignId"] == p["id"]), None)
            if match:
                obj.placement_bidding = match["placements"]

    return _finalize(session, profile_id, planned, ok_ids, errors, source, rule_id, local)


def _execute_add_products(session, profile_id, payload, planned, source, rule_id):
    """Tworzy reklamy produktowe w istniejacej grupie."""
    campaign_id = payload["campaign_id"]
    ad_group_id = payload["ad_group_id"]
    skus = [p["new"] for p in planned if p.get("kind") == "SKU"]
    asins = [p["new"] for p in planned if p.get("kind") == "ASIN"]

    with AdsClient(profile_id=profile_id) as client:
        wynik = entities.create_product_ads(client, campaign_id, ad_group_id, skus, asins)

    udane = len(wynik.get("success", []))
    bledy = wynik.get("error", [])

    for p in planned:
        _log_change(
            session, profile_id, "PRODUCT_AD", p["id"], "product_ad",
            None, p["new"], source,
            "APPLIED" if not bledy else "PARTIAL", rule_id,
        )

    # Nowe reklamy dociagnie najblizsza synchronizacja - nie zgadujemy ich
    # identyfikatorow, bo Amazon nadaje je po swojemu.
    return {
        "applied": udane,
        "failed": len(bledy),
        "errors": [str(b)[:200] for b in bledy[:5]],
        "planned": planned,
    }


def _execute_product_ads(session, profile_id, payload, planned, source, rule_id):
    """Wstrzymanie/wznowienie reklam produktowych - SP i SD osobno.

    SB nie ma reklam produktowych (ma kreacje), wiec pozycja z SB dostaje
    czytelny powod zamiast bledu z niewlasciwego zasobu.
    """
    from ..ads import sd_entities

    state = payload["state"]
    info = _po_typie(session, ProductAd, [str(p["id"]) for p in planned])
    grupy: dict[str, list[str]] = {}
    for p in planned:
        grupy.setdefault(info.get(str(p["id"]), ("SP",))[0], []).append(str(p["id"]))

    ok_ids: set[str] = set()
    errors: dict[str, str] = {}
    with AdsClient(profile_id=profile_id) as client:
        for prod, czesc in grupy.items():
            if prod == "SP":
                wynik = entities.set_product_ad_state(client, czesc, state)
            elif prod == "SD":
                wynik = sd_entities.set_product_ad_state(client, czesc, state)
            else:
                for i in czesc:
                    errors[i] = "Sponsored Brands nie ma reklam produktowych do wstrzymania"
                continue
            ok, err = _split_results(wynik, "adId")
            ok_ids |= ok
            errors |= err

    def local(p):
        obj = session.get(ProductAd, p["id"])
        if obj:
            obj.state = state

    return _finalize(session, profile_id, planned, ok_ids, errors, source, rule_id, local)


def _execute_remove_negatives(session, profile_id, payload, planned, source, rule_id):
    """Archiwizuje wykluczenia. Keywordy i ASIN-y ida osobnymi zasobami."""
    kw_grupa = [p["id"] for p in planned
                if p.get("co") == "KEYWORD" and (p.get("level") or "AD_GROUP") == "AD_GROUP"]
    kw_kampania = [p["id"] for p in planned
                   if p.get("co") == "KEYWORD" and p.get("level") == "CAMPAIGN"]
    tg = [p["id"] for p in planned if p.get("co") == "TARGET"]

    ok_ids: set[str] = set()
    errors: list[Any] = []

    with AdsClient(profile_id=profile_id) as client:
        for ids, poziom in ((kw_grupa, "AD_GROUP"), (kw_kampania, "CAMPAIGN")):
            if not ids:
                continue
            udane, bledy = _split_results(
                entities.set_negative_keyword_state(client, ids, "ARCHIVED", poziom),
                "keywordId",
            )
            ok_ids |= set(udane)
            errors.extend(bledy)
        if tg:
            udane, bledy = _split_results(
                entities.set_negative_target_state(client, tg, "ARCHIVED"), "targetId"
            )
            ok_ids |= set(udane)
            errors.extend(bledy)

    def local(p):
        model = NegativeKeyword if p.get("co") == "KEYWORD" else NegativeTarget
        obj = session.get(model, p["id"])
        if obj:
            obj.state = "ARCHIVED"

    return _finalize(session, profile_id, planned, ok_ids, errors, source, rule_id, local)


def _execute_negative_targets(session, profile_id, payload, planned, source, rule_id):
    items = [p["item"] for p in planned]
    with AdsClient(profile_id=profile_id) as client:
        result = entities.add_negative_targets(client, items)

    # Wykluczone ASIN-y dopisujemy lokalnie z tego samego powodu co frazy.
    # Czytelna postac wyrazenia budujemy tak samo, jak robi to synchronizacja,
    # zeby wiersz wygladal identycznie niezaleznie od tego, skad pochodzi.
    for item, nowy_id in _sparuj_z_odpowiedzia(items, result, "targetId"):
        if session.get(NegativeTarget, nowy_id):
            continue
        # Wyrazenie jest w planie (patrz `plan_negative_targets`); dla planow
        # sprzed tej zmiany odtwarzamy je z samego ASIN-u.
        wyrazenie = entities.wyrazenie_negatywu(item)
        pierwsze = wyrazenie[0] if isinstance(wyrazenie, list) and wyrazenie else {}
        session.add(NegativeTarget(
            target_id=nowy_id,
            ad_group_id=str(item["adGroupId"]) if item.get("adGroupId") else None,
            campaign_id=str(item.get("campaignId") or ""),
            profile_id=profile_id,
            level=payload.get("level", "AD_GROUP"),
            # Typ bierzemy z wyrazenia, nie z pola, ktorego zadanie nie niesie -
            # inaczej kazde wykluczenie ladowalo w bazie jako "MANUAL".
            expression_type=(pierwsze.get("type") if isinstance(pierwsze, dict) else None) or "MANUAL",
            value=(
                f"{pierwsze.get('type', '')} {pierwsze.get('value', '')}".strip()
                if isinstance(pierwsze, dict) else None
            ),
            state="ENABLED",
        ))

    applied = len(result.get("success", []))
    failed = len(result.get("error", []))
    for p in planned:
        _log_change(
            session, profile_id, p.get("entity_type", "NEGATIVE_ASIN"), p["id"],
            p.get("field", "asin"), None, p["new"],
            source, "APPLIED" if not failed else "PARTIAL", rule_id,
        )
    return {"applied": applied, "failed": failed, "errors": result.get("error", []),
            "planned": planned}



def _first_error(result: dict, co: str) -> str | None:
    """Czytelny opis pierwszego bledu z odpowiedzi Amazona."""
    bledy = result.get("error") or []
    if not bledy:
        return None
    pierwszy = bledy[0]
    powod = (
        pierwszy.get("errors")
        or pierwszy.get("message")
        or pierwszy.get("reason")
        or pierwszy
    )
    return f"{co}: {str(powod)[:300]}"


def _execute_create_campaign(session, profile_id, payload, planned, source, rule_id):
    """Tworzy kampanie z cala zawartoscia, krok po kroku.

    Kolejnosc wymusza sam Amazon: grupa potrzebuje identyfikatora kampanii,
    reklamy i slowa kluczowe - identyfikatora grupy. Nie da sie tego wyslac
    jednym zapytaniem.

    Kampania powstaje wstrzymana, a docelowy status ustawiamy dopiero po
    powodzeniu calej kaskady. Dzieki temu kampania przerwana w polowie - bez
    slow kluczowych albo bez produktow - nigdy nie zacznie wydawac. Nie
    probujemy tez niczego cofac: skasowanie polprodukty bywa gorsze niz jego
    zostawienie, a uzytkownik dostaje dokladna liste tego, co powstalo, i moze
    dokonczyc recznie albo zarchiwizowac.
    """
    spec = payload["spec"]
    utworzone: dict[str, Any] = {}
    bledy: list[str] = []

    with AdsClient(profile_id=profile_id) as client:
        wynik = entities.create_campaign(client, spec)
        campaign_id = entities._created_id(wynik, "campaignId")
        if not campaign_id:
            blad = _first_error(wynik, "Nie udało się utworzyć kampanii") or (
                "Nie udało się utworzyć kampanii: Amazon nie zwrócił identyfikatora"
            )
            return {"applied": 0, "failed": len(planned), "errors": [blad],
                    "planned": planned, "created": {}}

        utworzone["campaign_id"] = campaign_id
        log.info("Utworzono kampanie %s (profil %s)", campaign_id, profile_id)

        grupa = entities.create_ad_group(
            client, campaign_id, spec["ad_group"]["name"], spec["ad_group"]["default_bid"]
        )
        ad_group_id = entities._created_id(grupa, "adGroupId")
        if not ad_group_id:
            blad = _first_error(grupa, "Nie udało się utworzyć grupy reklam") or (
                "Nie udało się utworzyć grupy reklam"
            )
            _log_change(
                session, profile_id, "CAMPAIGN", campaign_id, "campaign",
                None, spec["name"], source, "PARTIAL", rule_id,
            )
            return {"applied": 1, "failed": len(planned) - 1, "errors": [blad],
                    "planned": planned, "created": utworzone}

        utworzone["ad_group_id"] = ad_group_id

        reklamy = entities.create_product_ads(
            client, campaign_id, ad_group_id, spec["skus"], spec["asins"]
        )
        utworzone["product_ads"] = len(reklamy.get("success") or [])
        if blad := _first_error(reklamy, "Część reklam produktowych nie powstała"):
            bledy.append(blad)

        if spec["targeting_type"] == "AUTO":
            cele = entities.create_auto_targets(
                client, campaign_id, ad_group_id, spec["auto_groups"],
                spec["ad_group"]["default_bid"],
            )
            utworzone["targets"] = len(cele.get("success") or [])
            if blad := _first_error(cele, "Część grup automatycznych nie powstała"):
                bledy.append(blad)
        else:
            if spec["keywords"]:
                slowa = entities.add_keywords(client, [
                    {"campaignId": campaign_id, "adGroupId": ad_group_id,
                     "keywordText": k["text"], "matchType": k["match_type"],
                     **({"bid": k["bid"]} if k["bid"] is not None else {})}
                    for k in spec["keywords"]
                ])
                utworzone["keywords"] = len(slowa.get("success") or [])
                if blad := _first_error(slowa, "Część słów kluczowych nie powstała"):
                    bledy.append(blad)

            if spec["product_targets"]:
                cele = entities.create_product_targets(
                    client, campaign_id, ad_group_id, spec["product_targets"]
                )
                utworzone["targets"] = len(cele.get("success") or [])
                if blad := _first_error(cele, "Część targetów produktowych nie powstała"):
                    bledy.append(blad)

        if spec["negative_keywords"]:
            wykluczenia = entities.add_negative_keywords(client, [
                {"campaignId": campaign_id, "adGroupId": ad_group_id,
                 "keywordText": n["text"], "matchType": n["match_type"]}
                for n in spec["negative_keywords"]
            ])
            utworzone["negatives"] = len(wykluczenia.get("success") or [])
            if blad := _first_error(wykluczenia, "Część wykluczeń nie powstała"):
                bledy.append(blad)

        # Status docelowy dopiero teraz - kampania niedokonczona zostaje wstrzymana
        if spec["final_state"] == "ENABLED" and not bledy:
            entities.set_campaign_state(client, [campaign_id], "ENABLED")
            utworzone["state"] = "ENABLED"
        else:
            utworzone["state"] = "PAUSED"

    status = "APPLIED" if not bledy else "PARTIAL"
    for item in planned:
        _log_change(
            session, profile_id, item.get("entity_type", "CAMPAIGN"),
            campaign_id if item["id"] == "nowa-kampania" else str(item["id"]),
            item.get("field", "?"), None, item.get("new"),
            source, status, rule_id,
        )

    if bledy and utworzone["state"] == "PAUSED" and spec["final_state"] == "ENABLED":
        bledy.append(
            "Kampania została wstrzymana zamiast uruchomiona, bo nie wszystko "
            "powstało — sprawdź ją i włącz ręcznie, gdy będzie kompletna."
        )

    return {
        "applied": len(planned) - len(bledy),
        "failed": len(bledy),
        "errors": bledy,
        "planned": planned,
        "created": utworzone,
    }


# --------------------------------------------------------------------------- #
# Odczyt
# --------------------------------------------------------------------------- #


# Etykiety pol - w kolejce ma byc widac, co sie zmienia, nie nazwe kolumny
FIELD_LABELS = {
    "bid": "stawka",
    "budget": "budżet dzienny",
    "state": "status",
    "keywordText": "wykluczenie",
    "new_keyword": "nowy keyword",
    "asin": "wykluczony ASIN",
    "campaign": "nowa kampania",
    "ad_group": "nowa grupa reklam",
    "product_ad": "produkt w kampanii",
    "new_target": "nowy target",
}

ENTITY_HINTS = {"PRODUCT_AD": "reklama produktu", "AD_GROUP": "grupa reklam"}

STATE_LABELS = {"ENABLED": "aktywna", "PAUSED": "wstrzymana", "ARCHIVED": "zarchiwizowana"}


def _entity_names(session: Session, planned: list[dict[str, Any]]) -> dict[str, str]:
    """Nazwy obiektow, ktorych dotyczy plan.

    Kolejka pokazywala same identyfikatory - z "10000000010003" nie da sie
    poznac, czy chodzi o kampanie brandowa, czy o testowa. Nazwy pobieramy
    jednym zapytaniem na typ, przy zatwierdzaniu, a nie przy tworzeniu planu:
    dzieki temu starsze plany tez sie rozwijaja.
    """
    by_type: dict[str, set[str]] = {}
    for item in planned:
        by_type.setdefault(item.get("entity_type") or "", set()).add(str(item.get("id")))

    names: dict[str, str] = {}

    def load(model, pk: str, name_col, ids: set[str]):
        if not ids:
            return
        rows = session.execute(
            select(getattr(model, pk), name_col).where(getattr(model, pk).in_(list(ids)))
        ).all()
        for key, value in rows:
            if value:
                names[str(key)] = value


    # Plany zapisane przed poprawka klasyfikacji moga miec target SB/SD
    # oznaczony jako KEYWORD. Zamiast zostawiac je bez nazwy - i zmuszac do
    # kasowania kolejki - szukamy identyfikatora w obu tabelach.
    niejednoznaczne = by_type.get("KEYWORD", set()) | by_type.get("TARGET", set())
    if niejednoznaczne:
        by_type.setdefault("KEYWORD", set()).update(niejednoznaczne)
        by_type.setdefault("TARGET", set()).update(niejednoznaczne)

    load(Campaign, "campaign_id", Campaign.name, by_type.get("CAMPAIGN", set()))
    load(ProductAd, "ad_id", ProductAd.sku, by_type.get("PRODUCT_AD", set()))
    load(Keyword, "keyword_id", Keyword.keyword_text, by_type.get("KEYWORD", set()))

    # Targety opisujemy tak samo jak w tabelach - surowe wyrazenie JSON
    # ("[{\"type\": \"ASIN_CATEGORY_SAME_AS\"...") nic nie mowi przy zatwierdzaniu
    target_ids = by_type.get("TARGET", set())
    if target_ids:
        from ..etl.labels import load_labels

        rows = session.execute(
            select(Target.target_id, Target.expression, Target.expression_type,
                   Target.resolved_expression, Target.ad_product,
                   Profile.marketplace_id)
            .join(Profile, Profile.profile_id == Target.profile_id, isouter=True)
            .where(Target.target_id.in_(list(target_ids)))
        ).all()
        cache: dict[str, dict] = {}
        for tid, expr, expr_type, rozwiniete, produkt, marketplace in rows:
            # SB i SD maja wlasny format wyrazen - slownik kategorii SP ich nie
            # rozwinie, za to zapisujemy gotowy opis przy pobieraniu encji.
            if (produkt or "SP") != "SP":
                names[str(tid)] = rozwiniete or expr_type or "target"
                continue
            if marketplace and marketplace not in cache:
                cache[marketplace] = load_labels(marketplace)
            labels = cache.get(marketplace or "", {})
            described = targeting_names.describe_expression(expr, labels)
            names[str(tid)] = described or (expr_type or "target")
    # Negatywy i nowe keywordy sa opisane identyfikatorem grupy reklam;
    # przy wstrzymaniu grupy identyfikator jest wprost jej wlasny.
    load(
        AdGroup, "ad_group_id", AdGroup.name,
        by_type.get("NEGATIVE", set()) | by_type.get("KEYWORD", set())
        | by_type.get("NEGATIVE_ASIN", set()) | by_type.get("AD_GROUP", set()),
    )
    return names


def _entity_context(session: Session, planned: list[dict[str, Any]]) -> dict[str, str]:
    """W ktorej kampanii siedzi kazdy obiekt planu.

    Sama nazwa nie wystarcza do rozpoznania zmiany: "Scisle powiazane" to
    auto-target, ktory istnieje w kazdej kampanii automatycznej, wiec wiersz
    "Target / Scisle powiazane / 146876045362112" nie mowi nic. Dopiero nazwa
    kampanii robi z tego konkret.
    """
    by_type: dict[str, set[str]] = {}
    for item in planned:
        by_type.setdefault(item.get("entity_type") or "", set()).add(str(item.get("id")))

    # obiekt -> kampania
    do_kampanii: dict[str, str] = {}

    def zbierz(model, pk: str, typy: set[str]):
        ids = set()
        for t in typy:
            ids |= by_type.get(t, set())
        if not ids:
            return
        rows = session.execute(
            select(getattr(model, pk), model.campaign_id).where(
                getattr(model, pk).in_(list(ids))
            )
        ).all()
        for key, cid in rows:
            if cid:
                do_kampanii[str(key)] = str(cid)

    # Tak samo jak przy nazwach: plany sprzed poprawki klasyfikacji moga miec
    # target SB/SD podpisany jako KEYWORD, wiec pytamy obie tabele. Kolizja
    # identyfikatorow miedzy nimi nie wystepuje - Amazon nadaje je globalnie.
    obie = by_type.get("KEYWORD", set()) | by_type.get("TARGET", set())
    if obie:
        by_type["KEYWORD"] = obie
        by_type["TARGET"] = obie

    zbierz(Target, "target_id", {"TARGET"})
    zbierz(Keyword, "keyword_id", {"KEYWORD"})
    zbierz(ProductAd, "ad_id", {"PRODUCT_AD"})
    # Negatywy i nowe keywordy plan opisuje identyfikatorem grupy reklam.
    zbierz(AdGroup, "ad_group_id", {"NEGATIVE", "NEGATIVE_ASIN", "KEYWORD", "AD_GROUP"})

    if not do_kampanii:
        return {}

    nazwy_kampanii = {
        str(cid): nazwa
        for cid, nazwa in session.execute(
            select(Campaign.campaign_id, Campaign.name).where(
                Campaign.campaign_id.in_(list(set(do_kampanii.values())))
            )
        ).all()
        if nazwa
    }

    return {
        eid: nazwy_kampanii[cid]
        for eid, cid in do_kampanii.items()
        if cid in nazwy_kampanii
    }


def _describe_item(
    item: dict[str, Any],
    names: dict[str, str],
    context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Uzupelnia pozycje planu o czytelna nazwe, kampanie i opis zmiany."""
    out = dict(item)
    entity_id = str(item.get("id"))
    if item.get("entity_type") != "CAMPAIGN":
        out["context"] = (context or {}).get(entity_id) or item.get("context")
    # Nazwe rozwiazujemy z bazy, ale nie kasujemy tej, ktora plan juz niesie:
    # encje jeszcze nieutworzone (nowa kampania, nowa grupa) nie maja czego
    # szukac w bazie, a nazwe znamy z opisu.
    out["entity_name"] = names.get(entity_id) or item.get("entity_name")

    field = item.get("field") or ""
    out["field_label"] = FIELD_LABELS.get(field, field)
    if field.startswith("placement:"):
        out["field_label"] = f"modyfikator: {field.split(':', 1)[1]}"

    if field == "state":
        out["old_label"] = STATE_LABELS.get(str(item.get("old")), item.get("old"))
        out["new_label"] = STATE_LABELS.get(str(item.get("new")), item.get("new"))
    return out


def pending_plans(session: Session, profile_id: int | None = None, limit: int = 50):
    stmt = (
        select(PendingPlan)
        .where(PendingPlan.status == "PENDING")
        .order_by(PendingPlan.created_at.desc())
        .limit(limit)
    )
    if profile_id:
        stmt = stmt.where(PendingPlan.profile_id == profile_id)
    now = utcnow()
    plans = session.execute(stmt).scalars().all()

    out = []
    for p in plans:
        planned = (p.payload or {}).get("planned", [])
        # Nazwy pobieramy raz na plan, nie raz na pozycje
        names = _entity_names(session, planned)
        context = _entity_context(session, planned)
        out.append({
            "plan_id": p.id,
            "profile_id": p.profile_id,
            "kind": p.kind,
            "summary": p.summary,
            "item_count": p.item_count,
            "created_at": iso_utc(p.created_at),
            # Plany nie wygasaja - pole zostaje dla zgodnosci, ale zawsze jest
            # falszem. Zamiast tego podajemy wiek, zeby dalo sie zauwazyc plan
            # lezacy od tygodnia.
            "expired": False,
            "age_minutes": int((now - p.created_at).total_seconds() // 60),
            "source": (p.payload or {}).get("source", "MANUAL"),
            "planned": [_describe_item(item, names, context) for item in planned],
        })
    return out


def recent_changes(session: Session, profile_id: int | None = None, limit: int = 200):
    stmt = select(ChangeLog).order_by(ChangeLog.ts.desc()).limit(limit)
    if profile_id:
        stmt = stmt.where(ChangeLog.profile_id == profile_id)
    return [
        {
            "id": c.id,
            "ts": iso_utc(c.ts),
            "profile_id": c.profile_id,
            "entity_type": c.entity_type,
            "entity_id": c.entity_id,
            "field": c.field,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "source": c.source,
            "status": c.status,
            "error": c.error,
        }
        for c in session.execute(stmt).scalars().all()
    ]

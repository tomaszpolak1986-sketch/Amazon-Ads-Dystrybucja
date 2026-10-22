"""Co się działo po wprowadzonych zmianach.

Panel do tej pory kończył pracę w chwili zatwierdzenia: zmiana szła do Amazona
i znikała z widoku. Nie było jak sprawdzić, czy podniesienie stawki sprzed
dwoch tygodni cokolwiek dalo - a bez tego kazda kolejna decyzja jest tak samo
niepewna jak pierwsza.

Trzy zastrzezenia, bez ktorych ten modul klamalby w dobrej wierze:

1. **To nie jest dowod przyczyny.** Rownolegle zmienia sie sezon, konkurencja
   i ceny. Modul pokazuje, co bylo potem - nie, co zmiana spowodowala. Dlatego
   nigdzie nie pada slowo "poprawilo"; jest "po zmianie".

2. **Zmiany nakladaja sie na siebie.** Jesli w oknie pomiaru ta sama kampania
   dostala kolejna zmiane, wynik dotyczy ich obu. Takie przypadki oznaczamy
   i nie wliczamy do podsumowania.

3. **Ocena wymaga czasu.** Amazon dolicza zamowienia przez 7 dni, wiec zmiana
   sprzed trzech dni nie ma jeszcze czego pokazac. Zamiast liczyc ja mimo to,
   trzymamy ja w poczekalni i mowimy, kiedy bedzie gotowa.
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import actions as actions_mod
from ..models import CampaignDaily, ChangeLog, TargetDaily

# Ile dni po zmianie musi uplynac, zeby wynik cokolwiek znaczyl. Krocej niz
# okno atrybucji Amazona nie ma sensu - sprzedaz bylaby niedoliczona.
MIN_DAYS_AFTER = 7

# Dlugosc okna przed i po zmianie
WINDOW_DAYS = 14

# Pola, ktorych skutek da sie zmierzyc w danych dziennych. Tworzenie kampanii
# albo dodanie keyworda nie ma "przed", wiec tu nie pasuje.
MIERZALNE = {"bid", "budget", "state"}


def _mierzalne(field: str) -> bool:
    return field in MIERZALNE or field.startswith("placement:")


def _etykieta(field: str) -> str:
    if field.startswith("placement:"):
        return f"modyfikator: {field.split(':', 1)[1]}"
    return {"bid": "stawka", "budget": "budżet dzienny", "state": "status"}.get(
        field, field
    )


def _liczba(tekst: str | None) -> float | None:
    """Wartosci w dzienniku sa tekstem - bywaja z procentem albo przecinkiem."""
    if tekst is None:
        return None
    czysty = str(tekst).replace("%", "").replace(",", ".").strip()
    try:
        return float(czysty)
    except ValueError:
        return None


def _agreguj(wiersze: list[Any]) -> dict[str, Any] | None:
    """Sumy i wskazniki z listy dni. None, gdy dni brak."""
    if not wiersze:
        return None
    dni = len(wiersze)
    koszt = sum(float(r.cost or 0) for r in wiersze)
    klik = sum(int(r.clicks or 0) for r in wiersze)
    sprzedaz = sum(float(r.sales or 0) for r in wiersze)
    zam = sum(int(r.orders or 0) for r in wiersze)
    wysw = sum(int(r.impressions or 0) for r in wiersze)
    return {
        "days": dni,
        "cost": round(koszt, 2),
        "clicks": klik,
        "sales": round(sprzedaz, 2),
        "orders": zam,
        "impressions": wysw,
        "daily_cost": round(koszt / dni, 2),
        "daily_sales": round(sprzedaz / dni, 2),
        "cpc": round(koszt / klik, 2) if klik else None,
        "acos": round(koszt / sprzedaz * 100, 1) if sprzedaz else None,
        "roas": round(sprzedaz / koszt, 2) if koszt else None,
    }


def analyze(
    session: Session,
    profile_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Zestawia kazda wprowadzona zmiane z tym, co dzialo sie przed i po niej."""
    stmt = select(func.max(CampaignDaily.date))
    if profile_id:
        stmt = stmt.where(CampaignDaily.profile_id == profile_id)
    ostatnie_dane = session.execute(stmt).scalar()
    if not ostatnie_dane:
        return {
            "min_days_after": MIN_DAYS_AFTER, "window_days": WINDOW_DAYS,
            "items": [], "waiting": [], "summary": [], "data_through": None,
        }

    zmiany_stmt = (
        select(ChangeLog)
        .where(ChangeLog.status.in_(("APPLIED", "PARTIAL")))
        .order_by(ChangeLog.ts.desc())
    )
    if profile_id:
        zmiany_stmt = zmiany_stmt.where(ChangeLog.profile_id == profile_id)
    zmiany = [z for z in session.execute(zmiany_stmt).scalars() if _mierzalne(z.field)]

    if not zmiany:
        return {
            "min_days_after": MIN_DAYS_AFTER, "window_days": WINDOW_DAYS,
            "items": [], "waiting": [], "summary": [],
            "data_through": ostatnie_dane.isoformat(),
        }

    # Ktore encje dostaly zmiane i kiedy - potrzebne do wykrycia nakladania.
    daty_zmian: dict[str, list[dt.date]] = {}
    for z in zmiany:
        daty_zmian.setdefault(z.entity_id, []).append(z.ts.date())

    najstarsza = min(z.ts.date() for z in zmiany) - dt.timedelta(days=WINDOW_DAYS)

    # Dane dzienne pobieramy raz, dla wszystkich encji naraz.
    id_kampanii = {z.entity_id for z in zmiany if z.entity_type == "CAMPAIGN"}
    id_targetow = {z.entity_id for z in zmiany if z.entity_type in ("TARGET", "KEYWORD")}

    serie: dict[str, dict[dt.date, Any]] = {}

    if id_kampanii:
        for r in session.execute(
            select(CampaignDaily).where(
                CampaignDaily.campaign_id.in_(list(id_kampanii)),
                CampaignDaily.date >= najstarsza,
            )
        ).scalars():
            serie.setdefault(r.campaign_id, {})[r.date] = r

    if id_targetow:
        for r in session.execute(
            select(TargetDaily).where(
                TargetDaily.target_id.in_(list(id_targetow)),
                TargetDaily.date >= najstarsza,
            )
        ).scalars():
            serie.setdefault(r.target_id, {})[r.date] = r

    # Nazwy i kampanie rozwiazujemy tak samo jak w kolejce zmian - inaczej
    # target pokazalby sam identyfikator, z ktorego nic nie wynika.
    opis = [{"entity_type": z.entity_type, "id": z.entity_id} for z in zmiany]
    nazwy = actions_mod._entity_names(session, opis)
    w_kampanii = actions_mod._entity_context(session, opis)

    gotowe: list[dict[str, Any]] = []
    czekaja: list[dict[str, Any]] = []

    for z in zmiany:
        dzien = z.ts.date()
        gotowa_od = dzien + dt.timedelta(days=MIN_DAYS_AFTER)

        if ostatnie_dane < gotowa_od:
            czekaja.append({
                "id": z.id,
                "entity_type": z.entity_type,
                "entity_id": z.entity_id,
                "entity_name": nazwy.get(z.entity_id),
                "campaign": w_kampanii.get(z.entity_id),
                "field": z.field,
                "field_label": _etykieta(z.field),
                "old": z.old_value,
                "new": z.new_value,
                "changed_on": dzien.isoformat(),
                "ready_on": gotowa_od.isoformat(),
                "days_left": (gotowa_od - ostatnie_dane).days,
                "source": z.source,
            })
            continue

        dane = serie.get(z.entity_id) or {}
        # Dzien zmiany pomijamy - jest mieszanka stanu przed i po.
        przed_dni = [dzien - dt.timedelta(days=i) for i in range(1, WINDOW_DAYS + 1)]
        po_dni = [dzien + dt.timedelta(days=i) for i in range(1, WINDOW_DAYS + 1)]
        po_dni = [d for d in po_dni if d <= ostatnie_dane]

        przed = _agreguj([dane[d] for d in przed_dni if d in dane])
        po = _agreguj([dane[d] for d in po_dni if d in dane])
        if not przed or not po:
            continue

        # Czy w oknie pomiaru doszla kolejna zmiana tej samej encji.
        inne = [
            d for d in daty_zmian.get(z.entity_id, [])
            if d != dzien and dzien < d <= max(po_dni)
        ]

        def delta(klucz: str) -> float | None:
            a, b = przed.get(klucz), po.get(klucz)
            if a in (None, 0) or b is None:
                return None
            return round((b - a) / abs(a) * 100, 1)

        gotowe.append({
            "id": z.id,
            "entity_type": z.entity_type,
            "entity_id": z.entity_id,
            "entity_name": nazwy.get(z.entity_id),
                "campaign": w_kampanii.get(z.entity_id),
            "field": z.field,
            "field_label": _etykieta(z.field),
            "old": z.old_value,
            "new": z.new_value,
            "old_num": _liczba(z.old_value),
            "new_num": _liczba(z.new_value),
            "changed_on": dzien.isoformat(),
            "source": z.source,
            "before": przed,
            "after": po,
            "delta": {
                "daily_cost": delta("daily_cost"),
                "daily_sales": delta("daily_sales"),
                "acos": delta("acos"),
                "cpc": delta("cpc"),
                "roas": delta("roas"),
            },
            # Wynik dotyczy tez tamtych zmian, wiec nie mowi nic o tej jednej.
            "confounded": bool(inne),
            "other_changes": len(inne),
        })

    # Podsumowanie po rodzaju zmiany - tylko z przypadkow niezakloconych.
    grupy: dict[str, list[dict[str, Any]]] = {}
    for g in gotowe:
        if not g["confounded"]:
            grupy.setdefault(g["field_label"], []).append(g)

    podsumowanie = []
    for etykieta, lista in sorted(grupy.items(), key=lambda kv: -len(kv[1])):
        acosy = [g["delta"]["acos"] for g in lista if g["delta"]["acos"] is not None]
        koszty = [g["delta"]["daily_cost"] for g in lista
                  if g["delta"]["daily_cost"] is not None]
        podsumowanie.append({
            "field_label": etykieta,
            "count": len(lista),
            "median_acos_delta": round(statistics.median(acosy), 1) if acosy else None,
            "median_cost_delta": round(statistics.median(koszty), 1) if koszty else None,
        })

    gotowe.sort(key=lambda g: g["changed_on"], reverse=True)
    czekaja.sort(key=lambda c: c["days_left"])

    return {
        "min_days_after": MIN_DAYS_AFTER,
        "window_days": WINDOW_DAYS,
        "data_through": ostatnie_dane.isoformat(),
        "items": gotowe[:limit],
        "waiting": czekaja[:limit],
        "waiting_total": len(czekaja),
        "summary": podsumowanie,
    }

"""Pobieranie nazw kategorii i marek uzywanych w targetowaniu.

Amazon zwraca w targetach same identyfikatory. Zeby panel wygladal jak konsola
reklamowa, trzeba je rozwinac w nazwy - i robimy to raz, do slownika, zamiast
odpytywac API przy kazdym wyswietleniu tabeli.

Kategorie: jedno zapytanie zwraca cale drzewo dla rynku.
Marki: osobne zapytanie per kategoria, wiec pobieramy tylko dla tych kategorii,
ktore faktycznie wystepuja w targetach konta.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from sqlalchemy import func, select

from ..ads.client import AdsClient
from ..db import session_scope
from ..models import NegativeTarget, Profile, Target, TargetingLabel

log = logging.getLogger(__name__)

CATEGORY_TREE_PATH = "/sp/targets/categories"
REFINEMENTS_PATH = "/sp/targets/category/{cid}/refinements"

# W targetach identyfikatory siedza w wyrazeniach jako wartosci
CATEGORY_TYPES = {"ASIN_CATEGORY_SAME_AS"}
BRAND_TYPES = {"ASIN_BRAND_SAME_AS"}


def _flatten_tree(nodes: list[dict[str, Any]], parent: str | None = None):
    """Drzewo kategorii przychodzi zagniezdzone: id/na/ch."""
    for node in nodes:
        cid = str(node.get("id"))
        yield {"ext_id": cid, "name": node.get("na") or cid, "parent_id": parent}
        children = node.get("ch") or []
        if children:
            yield from _flatten_tree(children, cid)


def _upsert_labels(marketplace_id: str, kind: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with session_scope() as session:
        existing = {
            e
            for (e,) in session.execute(
                select(TargetingLabel.ext_id).where(
                    TargetingLabel.marketplace_id == marketplace_id,
                    TargetingLabel.kind == kind,
                )
            ).all()
        }
        # Ta sama marka wystepuje w kilku kategoriach, wiec odsiewamy duplikaty
        # takze WEWNATRZ paczki - sam warunek "nie ma w bazie" nie wystarcza
        # i konczylo sie to naruszeniem klucza unikalnego.
        widziane: set[str] = set()
        fresh = []
        for r in rows:
            eid = r["ext_id"]
            if eid in existing or eid in widziane:
                continue
            widziane.add(eid)
            fresh.append({
                "marketplace_id": marketplace_id,
                "kind": kind,
                "ext_id": eid,
                "name": (r["name"] or "")[:250],
                "parent_id": r.get("parent_id"),
            })
        if fresh:
            session.bulk_insert_mappings(TargetingLabel, fresh)
    if fresh:
        # Liczba wierszy sie zmienila, wiec cache i tak by sie uniewaznil -
        # ale kasujemy go od razu, zeby nie czekac na kolejne zapytanie.
        reset_cache()
    return len(fresh)


def used_ids(profile_id: int) -> tuple[set[str], set[str]]:
    """Identyfikatory kategorii i marek wystepujace w targetach tego profilu."""
    categories: set[str] = set()
    brands: set[str] = set()

    with session_scope() as session:
        rows = session.execute(
            select(Target.expression).where(Target.profile_id == profile_id)
        ).scalars().all()
        neg = session.execute(
            select(NegativeTarget.value).where(NegativeTarget.profile_id == profile_id)
        ).scalars().all()

    for expr in rows:
        if isinstance(expr, str):
            try:
                expr = json.loads(expr)
            except ValueError:
                continue
        for cond in expr or []:
            if not isinstance(cond, dict):
                continue
            kind, value = cond.get("type"), cond.get("value")
            if not value or not isinstance(value, (str, int)):
                continue
            if kind in CATEGORY_TYPES:
                categories.add(str(value))
            elif kind in BRAND_TYPES:
                brands.add(str(value))

    # negatywne targety trzymamy w postaci tekstowej "TYP wartosc"
    for text in neg:
        for kind, value in re.findall(r"(\w+)\s+(\d+)", text or ""):
            if kind in CATEGORY_TYPES:
                categories.add(value)
            elif kind in BRAND_TYPES:
                brands.add(value)

    return categories, brands


def sync_labels(profile_id: int, marketplace_id: str) -> dict[str, int]:
    """Uzupelnia slownik nazw dla jednego rynku."""
    counts = {"categories": 0, "brands": 0, "brand_lookups": 0}

    with AdsClient(profile_id=profile_id) as client:
        # --- drzewo kategorii ---
        try:
            raw = client.get(CATEGORY_TREE_PATH) or {}
            tree = raw.get("CategoryTree")
            if isinstance(tree, str):
                tree = json.loads(tree)
            counts["categories"] = _upsert_labels(
                marketplace_id, "CATEGORY", list(_flatten_tree(tree or []))
            )
        except Exception as exc:
            log.warning("Nie pobralem drzewa kategorii (%s): %s", marketplace_id, exc)

        # --- marki: tylko dla kategorii uzywanych w targetach ---
        categories, brands_needed = used_ids(profile_id)
        if not brands_needed:
            return counts

        with session_scope() as session:
            known = {
                e
                for (e,) in session.execute(
                    select(TargetingLabel.ext_id).where(
                        TargetingLabel.marketplace_id == marketplace_id,
                        TargetingLabel.kind == "BRAND",
                    )
                ).all()
            }
        missing = brands_needed - known
        if not missing:
            return counts

        found: list[dict[str, Any]] = []
        for cid in sorted(categories):
            if not missing:
                break
            try:
                data = client.get(REFINEMENTS_PATH.format(cid=cid)) or {}
            except Exception as exc:
                log.debug("Brak uszczegolowien dla kategorii %s: %s", cid, exc)
                continue
            counts["brand_lookups"] += 1
            for b in data.get("brands") or []:
                bid = str(b.get("id"))
                if bid in missing:
                    found.append({"ext_id": bid, "name": b.get("name"), "parent_id": cid})
                    missing.discard(bid)

        counts["brands"] = _upsert_labels(marketplace_id, "BRAND", found)
        if missing:
            log.info(
                "Rynek %s: nie rozwiazano %s marek - nie wystepuja w uszczegolowieniach "
                "kategorii, ktore targetujesz", marketplace_id, len(missing),
            )

    return counts


def sync_all_labels() -> dict[str, Any]:
    with session_scope() as session:
        profiles = session.execute(
            select(Profile.profile_id, Profile.marketplace_id, Profile.country_code)
        ).all()

    summary = {}
    for pid, marketplace, country in profiles:
        if not marketplace:
            continue
        try:
            summary[country or str(pid)] = sync_labels(pid, marketplace)
        except Exception as exc:
            log.exception("Slownik nazw dla %s nie powiodl sie", country)
            summary[country or str(pid)] = {"error": str(exc)[:200]}
    return summary


# Slowniki nazw trzymamy w pamieci procesu.
#
# To dane referencyjne Amazona - drzewo kategorii i marki - ktore zmieniaja sie
# raz na wiele tygodni, a jest ich cwierc miliona wierszy. Budowanie tego
# slownika przy kazdym wejsciu na Sugestie kosztowalo 2,3 s, i to zanim
# policzylismy cokolwiek.
#
# Waznosc sprawdzamy liczba wierszy, a nie czasem: pobranie etykiet potrafi
# zrobic osobny proces (CLI), wiec licznik w pamieci nic by nie dal. COUNT
# kosztuje 0,7 ms na rynek wobec 2,3 s odbudowy - stac nas na to przy kazdym
# wywolaniu.
_CACHE: dict[str, tuple[int, dict[tuple[str, str], str]]] = {}
_LOCK = threading.Lock()

# Klucz pod ktorym trzymamy slownik scalony ze wszystkich rynkow
_WSZYSTKIE = "*"


def marki(session, marketplace_id: str, szukaj: str | None = None,
          limit: int = 60) -> list[dict[str, str]]:
    """Marki znane dla rynku - do wyboru przy wykluczaniu.

    Czytamy ze slownika, nie z Amazona: lista marek w kategorii nie zmienia
    sie z godziny na godzine, a kazde zapytanie to osobne wywolanie API.
    Slownik napelnia `dociagnij_marki`.
    """
    stmt = select(
        TargetingLabel.ext_id, TargetingLabel.name, TargetingLabel.parent_id
    ).where(
        TargetingLabel.marketplace_id == marketplace_id,
        TargetingLabel.kind == "BRAND",
    )
    if szukaj:
        stmt = stmt.where(TargetingLabel.name.ilike(f"%{szukaj.strip()}%"))
    rows = session.execute(stmt.order_by(TargetingLabel.name).limit(limit)).all()

    # Z ktorej kategorii marka pochodzi - zeby bylo widac, ze to konkurencja
    # z tej samej polki, a nie przypadkowa nazwa z calego Amazona.
    nazwy = _zbuduj(session, marketplace_id)
    return [
        {
            "brand_id": e,
            "name": n,
            "category": nazwy.get(("CATEGORY", str(pid))) if pid else None,
        }
        for e, n, pid in rows
    ]


# Ile wlasnych ASIN-ow pytamy o kategorie. Produkty jednego sprzedawcy
# skupiaja sie w kilku wezlach, wiec probka wystarcza - a kazde 20 ASIN-ow
# to osobne zapytanie do SP-API.
PROBKA_ASINOW = 200


def kategorie_naszych_produktow(profile_id: int, marketplace_id: str) -> set[str]:
    """Wezly kategorii, w ktorych leza NASZE reklamowane produkty.

    To wlasciwe zrodlo marek do wykluczania: konkurenci siedza tam, gdzie
    lezy nasz asortyment, a nie tylko tam, gdzie akurat celujemy targetem.
    Kampanie automatyczne i keywordowe nie maja zadnej kategorii, wiec bez
    tego kroku slownik marek byl dla nich pusty.

    Kategorie bierzemy z SP-API (`classifications` w katalogu) - Ads API ich
    nie zna. Identyfikator jest ten sam w obu interfejsach, wiec nadaje sie
    wprost do zapytania o marki.
    """
    from ..config import settings

    if not settings.sp_configured:
        return set()

    from ..sp.client import SpClient
    from ..models import ProductAd

    with session_scope() as session:
        asiny = [
            a for (a,) in session.execute(
                select(ProductAd.asin)
                .where(ProductAd.profile_id == profile_id, ProductAd.asin.is_not(None))
                .distinct()
                .limit(PROBKA_ASINOW)
            ).all()
        ]
    if not asiny:
        return set()

    wezly: set[str] = set()
    try:
        with SpClient() as client:
            for i in range(0, len(asiny), 20):
                partia = asiny[i:i + 20]
                dane = client.get(
                    "/catalog/2022-04-01/items",
                    params={
                        "identifiers": ",".join(partia),
                        "identifiersType": "ASIN",
                        "marketplaceIds": marketplace_id,
                        "includedData": "classifications",
                        "pageSize": 20,
                    },
                ) or {}
                for item in dane.get("items") or []:
                    for grupa in item.get("classifications") or []:
                        # Odpowiedz bywa zagniezdzona: lista klasyfikacji,
                        # a w kazdej wlasna lista wezlow.
                        for wezel in grupa.get("classifications") or [grupa]:
                            cid = wezel.get("classificationId")
                            if cid:
                                wezly.add(str(cid))
    except Exception as exc:
        log.warning("Nie pobralem kategorii wlasnych produktow (%s): %s", marketplace_id, exc)

    return wezly


def dociagnij_marki(profile_id: int, marketplace_id: str) -> dict[str, int]:
    """Uzupelnia slownik marek z uszczegolowien kategorii.

    Amazon nie ma wyszukiwarki marek po nazwie - jedyne zrodlo to lista marek
    obecnych w konkretnej kategorii. Kategorie bierzemy z dwoch stron:

    * **gdzie lezy nasz asortyment** (SP-API) - to wsrod tych marek siedza
      konkurenci, obok ktorych wyswietlaja sie nasze reklamy;
    * **co targetujemy** (Ads API) - kategorie wskazane recznie w kampaniach
      produktowych, nawet jesli sami w nich nie sprzedajemy.

    Bez pierwszego zrodla konto bez kampanii produktowych - same automaty
    i keywordy - nie dostawalo zadnych marek.
    """
    targetowane, _ = used_ids(profile_id)
    wlasne = kategorie_naszych_produktow(profile_id, marketplace_id)
    kategorie = wlasne | targetowane
    if not kategorie:
        return {"categories": 0, "own_categories": 0, "brands": 0}

    znalezione: list[dict[str, Any]] = []
    with AdsClient(profile_id=profile_id) as client:
        for cid in sorted(kategorie):
            try:
                dane = client.get(REFINEMENTS_PATH.format(cid=cid)) or {}
            except Exception as exc:
                log.debug("Brak uszczegolowien dla kategorii %s: %s", cid, exc)
                continue
            for b in dane.get("brands") or []:
                znalezione.append({
                    "ext_id": str(b.get("id")), "name": b.get("name"), "parent_id": cid,
                })
    nowe = _upsert_labels(marketplace_id, "BRAND", znalezione)
    log.info(
        "Marki (%s): %s kategorii (%s z wlasnego asortymentu), %s nowych",
        marketplace_id, len(kategorie), len(wlasne), nowe,
    )
    return {"categories": len(kategorie), "own_categories": len(wlasne), "brands": nowe}


def _zbuduj(session, marketplace_id: str | None) -> dict[tuple[str, str], str]:
    stmt = select(TargetingLabel.kind, TargetingLabel.ext_id, TargetingLabel.name)
    if marketplace_id:
        stmt = stmt.where(TargetingLabel.marketplace_id == marketplace_id)
    return {(k, e): n for k, e, n in session.execute(stmt).all()}


def labels_for(session, marketplace_id: str | None) -> dict[tuple[str, str], str]:
    """Slownik (kind, ext_id) -> nazwa. Bez rynku - scalony ze wszystkich.

    Zwracany slownik jest wspoldzielony miedzy wywolaniami, wiec nie wolno go
    modyfikowac w miejscu.
    """
    klucz = marketplace_id or _WSZYSTKIE

    licznik = select(func.count()).select_from(TargetingLabel)
    if marketplace_id:
        licznik = licznik.where(TargetingLabel.marketplace_id == marketplace_id)
    ile = session.execute(licznik).scalar() or 0

    with _LOCK:
        wpis = _CACHE.get(klucz)
        if wpis and wpis[0] == ile:
            return wpis[1]

    slownik = _zbuduj(session, marketplace_id)
    with _LOCK:
        _CACHE[klucz] = (ile, slownik)
    return slownik


def reset_cache() -> None:
    """Kasuje slowniki - wolane po pobraniu swiezych etykiet."""
    with _LOCK:
        _CACHE.clear()


def load_labels(marketplace_id: str) -> dict[tuple[str, str], str]:
    """Slownik (kind, ext_id) -> nazwa, do uzycia przy wyswietlaniu."""
    with session_scope() as session:
        return labels_for(session, marketplace_id)

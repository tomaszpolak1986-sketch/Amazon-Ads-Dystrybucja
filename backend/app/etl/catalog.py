"""Pobieranie danych katalogowych - tytulow i miniatur produktow.

Sluzy do tego, zeby w panelu widziec produkt, a nie sam kod SKU. Przy widoku
"ten sam produkt w kilku kampaniach" miniatura mowi od razu, o co chodzi -
kod `FBA-800-18-BIALY-5-NEW` nie mowi nic.

Katalog odpytujemy wsadowo (do 20 ASIN-ow naraz) i zapamietujemy. Zdjecia
zmieniaja sie rzadko, wiec odswiezamy dopiero po miesiacu.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (
    CatalogItem, Listing, NegativeTarget, ProductAd, Profile, SearchTermDaily,
)
from ..sp.client import SpApiError, SpClient
from ..timeutil import utcnow

log = logging.getLogger(__name__)

# Ile ASIN-ow w jednym zapytaniu. Amazon przyjmuje 20.
BATCH = 20

# Po ilu dniach dane katalogowe uznajemy za wymagajace odswiezenia
STALE_DAYS = 30

# Miniatura: najmniejszy obrazek, ktory jeszcze cos pokazuje. Wieksze linki
# trzymamy osobno, gdyby przydal sie podglad.
MAX_THUMB_PX = 160


def _wybierz_obrazy(item: dict[str, Any]) -> tuple[str | None, str | None]:
    """Zwraca (miniatura, obraz glowny)."""
    wszystkie: list[dict[str, Any]] = []
    for grupa in item.get("images") or []:
        wszystkie.extend(grupa.get("images") or [])

    glowne = [i for i in wszystkie if (i.get("variant") or "").upper() == "MAIN"]
    kandydaci = glowne or wszystkie
    if not kandydaci:
        return None, None

    male = [i for i in kandydaci if (i.get("height") or 9999) <= MAX_THUMB_PX]
    miniatura = min(male, key=lambda i: i.get("height") or 0) if male else None
    duzy = max(kandydaci, key=lambda i: i.get("height") or 0)

    return (miniatura or duzy).get("link"), duzy.get("link")


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def missing_asins(
    session: Session, profile_id: int, marketplace_id: str, only_stale: bool = True
) -> list[str]:
    """ASIN-y reklamowane na tym rynku, dla ktorych nie mamy swiezych danych."""
    reklamowane = {
        a for a, in session.execute(
            select(ProductAd.asin).where(
                ProductAd.profile_id == profile_id,
                ProductAd.asin.is_not(None),
            ).distinct()
        ).all() if a
    }
    if not reklamowane:
        return []

    warunki = [CatalogItem.marketplace_id == marketplace_id]
    if only_stale:
        warunki.append(CatalogItem.fetched_at >= utcnow() - dt.timedelta(days=STALE_DAYS))

    mamy = {
        a for a, in session.execute(
            select(CatalogItem.asin).where(*warunki)
        ).all()
    }
    return sorted(reklamowane - mamy)


# ASIN ma staly ksztalt: B + 9 znakow alfanumerycznych. W raporcie fraz
# wyszukiwania Amazon zapisuje je mala litera.
ASIN_WZOR = re.compile(r"^b[0-9a-z]{9}$", re.I)


def wszystkie_potrzebne(
    session: Session, profile_id: int, marketplace_id: str, only_stale: bool = True
) -> list[str]:
    """Wszystkie ASIN-y, ktore panel gdziekolwiek pokazuje.

    Poza wlasnymi produktami sa tu **ASIN-y konkurencji**: w kampaniach
    produktowych Amazon zapisuje je jako frazy wyszukiwania, a przy
    wykluczeniach - jako wyrazenia ASIN_SAME_AS. Bez tytulu i zdjecia
    `b093zsryyw` nie mowi nic, a to wlasnie na tej podstawie podejmuje sie
    decyzje o wykluczeniu.
    """
    potrzebne: set[str] = set()

    # 1. wlasne produkty - reklamowane i te z pelnej bazy ofert
    for kolumna, warunek in (
        (ProductAd.asin, ProductAd.profile_id == profile_id),
        (Listing.asin, Listing.marketplace_id == marketplace_id),
    ):
        potrzebne |= {
            a for a, in session.execute(
                select(kolumna).where(warunek, kolumna.is_not(None)).distinct()
            ).all() if a
        }

    # 2. ASIN-y wystepujace jako frazy wyszukiwania (targetowanie produktowe)
    frazy = session.execute(
        select(SearchTermDaily.search_term).where(
            SearchTermDaily.profile_id == profile_id,
            SearchTermDaily.search_term.is_not(None),
        ).distinct()
    ).all()
    potrzebne |= {
        f.strip().upper() for f, in frazy if f and ASIN_WZOR.match(f.strip())
    }

    # 3. wykluczone ASIN-y
    wykluczone = session.execute(
        select(NegativeTarget.value).where(
            NegativeTarget.profile_id == profile_id,
            NegativeTarget.value.is_not(None),
        ).distinct()
    ).all()
    for w, in wykluczone:
        for slowo in (w or "").replace("_", " ").split():
            if ASIN_WZOR.match(slowo):
                potrzebne.add(slowo.upper())

    if not potrzebne:
        return []

    warunki = [CatalogItem.marketplace_id == marketplace_id]
    if only_stale:
        warunki.append(CatalogItem.fetched_at >= utcnow() - dt.timedelta(days=STALE_DAYS))
    mamy = {
        a for a, in session.execute(select(CatalogItem.asin).where(*warunki)).all()
    }
    return sorted(potrzebne - mamy)


def sync_market(client: SpClient, marketplace_id: str, asiny: list[str]) -> dict[str, Any]:
    """Pobiera i zapisuje dane katalogowe dla listy ASIN-ow."""
    zapisane = 0
    bledy: list[str] = []

    for partia in _chunks(asiny, BATCH):
        try:
            resp = client.get(
                "/catalog/2022-04-01/items",
                params={
                    "identifiers": ",".join(partia),
                    "identifiersType": "ASIN",
                    "marketplaceIds": marketplace_id,
                    "includedData": "images,summaries",
                    "pageSize": BATCH,
                },
            )
        except SpApiError as exc:
            bledy.append(str(exc)[:140])
            continue

        pozycje = resp.get("items") or []
        if not pozycje:
            continue

        with session_scope() as session:
            istniejace = {
                (r.asin, r.marketplace_id): r
                for r in session.execute(
                    select(CatalogItem).where(
                        CatalogItem.marketplace_id == marketplace_id,
                        CatalogItem.asin.in_([p.get("asin") for p in pozycje if p.get("asin")]),
                    )
                ).scalars()
            }

            for poz in pozycje:
                asin = poz.get("asin")
                if not asin:
                    continue
                podsumowanie = (poz.get("summaries") or [{}])[0]
                mini, duzy = _wybierz_obrazy(poz)

                rec = istniejace.get((asin, marketplace_id))
                if rec is None:
                    rec = CatalogItem(asin=asin, marketplace_id=marketplace_id)
                    session.add(rec)

                rec.title = (podsumowanie.get("itemName") or None)
                rec.brand = (podsumowanie.get("brand") or None)
                rec.image_small = mini
                rec.image_main = duzy
                rec.fetched_at = utcnow()
                zapisane += 1

    return {"stored": zapisane, "errors": bledy[:5], "error_count": len(bledy)}


def sync_all(
    profile_id: int | None = None,
    only_stale: bool = True,
    szeroko: bool = False,
) -> dict[str, Any]:
    """Uzupelnia katalog dla rynkow, ktore maja reklamowane produkty.

    `szeroko` obejmuje takze cala baze ofert oraz ASIN-y konkurencji z fraz
    wyszukiwania i wykluczen - bez nich `b093zsryyw` zostaje golym kodem.
    """
    with session_scope() as session:
        stmt = select(Profile.profile_id, Profile.country_code, Profile.marketplace_id)
        if profile_id:
            stmt = stmt.where(Profile.profile_id == profile_id)
        rynki = [(p, c or str(p), m) for p, c, m in session.execute(stmt).all() if m]

    podsumowanie: dict[str, Any] = {}
    with SpClient() as client:
        for pid, kraj, mp in rynki:
            with session_scope() as session:
                brakujace = (
                    wszystkie_potrzebne(session, pid, mp, only_stale)
                    if szeroko
                    else missing_asins(session, pid, mp, only_stale)
                )
            if not brakujace:
                continue
            log.info("Katalog %s: %s produktow do pobrania", kraj, len(brakujace))
            podsumowanie[kraj] = sync_market(client, mp, brakujace)
            log.info("Katalog %s: %s", kraj, podsumowanie[kraj])

    return podsumowanie


def lookup(
    session: Session, marketplace_id: str | None, asiny: list[str]
) -> dict[str, dict[str, Any]]:
    """Dane katalogowe gotowe do dolaczenia do wiersza tabeli."""
    if not asiny:
        return {}
    warunki = [CatalogItem.asin.in_(asiny)]
    if marketplace_id:
        warunki.append(CatalogItem.marketplace_id == marketplace_id)

    out: dict[str, dict[str, Any]] = {}
    for r in session.execute(select(CatalogItem).where(*warunki)).scalars():
        # Przy widoku laczonym moze byc kilka rynkow - pierwszy wygrywa,
        # bo miniatura tego samego ASIN-u jest wszedzie ta sama.
        out.setdefault(r.asin, {
            "title": r.title,
            "brand": r.brand,
            "image": r.image_small,
            "image_full": r.image_main,
        })
    return out

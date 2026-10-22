"""Zapis pełnej bazy produktowej z raportu ofert Seller Central."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import Listing, Profile
from ..sp.client import SpApiError, SpClient
from ..sp.listings_report import UkladRaportuZmieniony, fetch
from ..timeutil import utcnow

log = logging.getLogger(__name__)


def _zapisz(marketplace_id: str, oferty: list[dict[str, Any]]) -> dict[str, int]:
    """Wstawia nowe i aktualizuje istniejace oferty tego rynku."""
    if not oferty:
        return {"stored": 0, "new": 0}

    teraz = utcnow()
    nowe = 0
    with session_scope() as session:
        istniejace = {
            r.sku: r
            for r in session.execute(
                select(Listing).where(Listing.marketplace_id == marketplace_id)
            ).scalars()
        }
        for o in oferty:
            rec = istniejace.get(o["sku"])
            if rec is None:
                rec = Listing(sku=o["sku"], marketplace_id=marketplace_id)
                session.add(rec)
                nowe += 1
            for pole in ("asin", "title", "price", "quantity", "status",
                         "fulfillment", "listing_id", "opened_on"):
                setattr(rec, pole, o[pole])
            rec.fetched_at = teraz

    return {"stored": len(oferty), "new": nowe}


def uzupelnij_asiny() -> int:
    """Dopisuje brakujace ASIN-y z innych rynkow tego samego konta.

    Raport dla Francji nie ma kolumny ASIN - jest tylko `Produkt-ID`, ktore
    przy wiekszosci ofert zawiera EAN. Bez ASIN-u produkt traci miniature
    i tytul katalogowy, wiec probujemy odzyskac go z rynku, ktory go podal.

    Uzupelniamy **tylko wtedy, gdy wszystkie pozostale rynki sa zgodne**. Na
    tym koncie 97 SKU ma sprzeczne ASIN-y miedzy rynkami - dla nich zgadywanie
    podpieloby pod produkt cudze zdjecie, wiec zostawiamy pusto.
    """
    uzupelnione = 0
    with session_scope() as session:
        wszystkie = session.execute(
            select(Listing.sku, Listing.asin).where(Listing.asin.is_not(None))
        ).all()

        kandydaci: dict[str, set[str]] = {}
        for sku, asin in wszystkie:
            kandydaci.setdefault(sku, set()).add(asin)
        # Jednomyslne to te, gdzie rynki nie sa ze soba sprzeczne.
        jednomyslne = {sku: next(iter(a)) for sku, a in kandydaci.items() if len(a) == 1}

        braki = session.execute(
            select(Listing).where(Listing.asin.is_(None))
        ).scalars().all()
        for rec in braki:
            asin = jednomyslne.get(rec.sku)
            if asin:
                rec.asin = asin
                rec.asin_inferred = True
                uzupelnione += 1

    if uzupelnione:
        log.info("Uzupelniono %s ASIN-ow z innych rynkow", uzupelnione)
    return uzupelnione


def sync_all(profile_id: int | None = None) -> dict[str, Any]:
    """Pobiera oferty dla rynkow, na ktorych cokolwiek reklamujemy.

    Rynki bez kampanii pomijamy - raport i tak by sie policzyl, ale
    kosztowalby minute oczekiwania na limit tworzenia raportow.
    """
    with session_scope() as session:
        stmt = select(Profile.profile_id, Profile.country_code, Profile.marketplace_id)
        if profile_id:
            stmt = stmt.where(Profile.profile_id == profile_id)
        rynki = [(p, c or str(p), m) for p, c, m in session.execute(stmt).all() if m]

    podsumowanie: dict[str, Any] = {}
    with SpClient() as client:
        for _pid, kraj, mp in rynki:
            try:
                oferty = fetch(client, mp)
            except UkladRaportuZmieniony as exc:
                # Uklad rozni sie miedzy rynkami - NL zwraca 28 kolumn, DE 32 -
                # wiec problem na jednym nie mowi nic o pozostalych. Idziemy
                # dalej i zglaszamy tylko ten rynek.
                log.error("Oferty %s: %s", kraj, exc)
                podsumowanie[kraj] = {"error": str(exc)}
                continue
            except SpApiError as exc:
                log.warning("Oferty %s: %s", kraj, exc)
                podsumowanie[kraj] = {"error": str(exc)[:200]}
                continue

            podsumowanie[kraj] = _zapisz(mp, oferty)
            log.info("Oferty %s: %s", kraj, podsumowanie[kraj])

    podsumowanie["_asin_z_innych_rynkow"] = uzupelnij_asiny()
    return podsumowanie

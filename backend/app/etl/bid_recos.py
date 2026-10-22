"""Pobieranie rekomendacji stawek od Amazona.

To te same widelki, ktore widac w panelu Ads przy kazdym slowie kluczowym:
sugerowana stawka i zakres od-do. Amazon liczy je pod konwersje i widocznosc,
nie pod nasz docelowy ACOS - dlatego sluza za punkt odniesienia obok wlasnych
sugestii, a nie za zalecenie do zastosowania wprost.

API przyjmuje wyrazenia wsadowo, ale zawsze w obrebie jednej grupy reklam, wiec
pobieramy grupa po grupie. Jest ostro limitowane (429 przy pierwszym strzale),
dlatego dziala jako zadanie w tle, a nie przy otwieraniu tabeli.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ads.client import AdsApiError, AdsClient
from ..db import session_scope
from ..models import BidRecommendation, Keyword, Target
from ..timeutil import utcnow

log = logging.getLogger(__name__)

MEDIA = "application/vnd.spthemebasedbidrecommendation.v3+json"
PATH = "/sp/targets/bid/recommendations"

# Ile wyrazen w jednym zapytaniu. Amazon przyjal 16 bez protestu; trzymamy
# rozsadny zapas, bo limit bywa zalezny od typu wyrazenia.
BATCH = 50

MATCH_TO_EXPRESSION = {
    "EXACT": "KEYWORD_EXACT_MATCH",
    "PHRASE": "KEYWORD_PHRASE_MATCH",
    "BROAD": "KEYWORD_BROAD_MATCH",
}

# Po ilu dniach rekomendacja jest na tyle stara, ze warto ja odswiezyc
STALE_DAYS = 7


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _fetch_group(
    client: AdsClient, campaign_id: str, ad_group_id: str,
    wyrazenia: list[dict[str, str]],
) -> dict[str, list[float]]:
    """Zwraca {tekst frazy: [dolna, sugerowana, gorna]}."""
    resp = client.request(
        "POST", PATH,
        json={
            "targetingExpressions": wyrazenia,
            "campaignId": str(campaign_id),
            "adGroupId": str(ad_group_id),
            "recommendationType": "BIDS_FOR_EXISTING_AD_GROUP",
        },
        accept=MEDIA, content_type=MEDIA,
    )

    out: dict[str, list[float]] = {}
    for blok in resp.get("bidRecommendations") or []:
        for poz in blok.get("bidRecommendationsForTargetingExpressions") or []:
            wartosci = [
                float(b["suggestedBid"])
                for b in (poz.get("bidValues") or [])
                if b.get("suggestedBid") is not None
            ]
            if not wartosci:
                continue
            klucz = (poz.get("targetingExpression") or {}).get("value")
            if klucz:
                out[str(klucz)] = sorted(wartosci)
    return out


def _store(
    session: Session, profile_id: int, entity_type: str,
    dane: dict[str, tuple[float, float, float]], theme: str | None = None,
) -> int:
    """Zapisuje rekomendacje, nadpisujac wczesniejsze dla tej samej encji."""
    istniejace = {
        r.entity_id: r
        for r in session.execute(
            select(BidRecommendation).where(
                BidRecommendation.profile_id == profile_id,
                BidRecommendation.entity_type == entity_type,
                BidRecommendation.entity_id.in_(list(dane)),
            )
        ).scalars()
    }

    teraz = utcnow()
    for eid, (lo, med, hi) in dane.items():
        rec = istniejace.get(eid)
        if rec is None:
            rec = BidRecommendation(
                profile_id=profile_id, entity_type=entity_type, entity_id=eid
            )
            session.add(rec)
        rec.low, rec.median, rec.high = lo, med, hi
        rec.theme = theme
        rec.fetched_at = teraz
    return len(dane)


def sync_keywords(
    profile_id: int, only_stale: bool = True, limit_groups: int | None = None
) -> dict[str, Any]:
    """Pobiera widelki dla slow kluczowych, grupa reklam po grupie."""
    with session_scope() as session:
        stmt = select(
            Keyword.keyword_id, Keyword.keyword_text, Keyword.match_type,
            Keyword.campaign_id, Keyword.ad_group_id,
        ).where(
            Keyword.profile_id == profile_id,
            Keyword.state == "ENABLED",
            Keyword.ad_group_id.is_not(None),
        )
        keywords = session.execute(stmt).all()

        swieze: set[str] = set()
        if only_stale:
            prog = utcnow() - dt.timedelta(days=STALE_DAYS)
            swieze = {
                r for r in session.execute(
                    select(BidRecommendation.entity_id).where(
                        BidRecommendation.profile_id == profile_id,
                        BidRecommendation.entity_type == "KEYWORD",
                        BidRecommendation.fetched_at >= prog,
                    )
                ).scalars()
            }

    # Grupujemy po grupie reklam, bo API wymaga jej identyfikatora
    grupy: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for kid, tekst, match, camp, ag in keywords:
        if kid in swieze or not tekst:
            continue
        typ = MATCH_TO_EXPRESSION.get((match or "").upper())
        if not typ:
            continue
        grupy.setdefault((str(camp), str(ag)), []).append((kid, tekst, typ))

    if limit_groups:
        grupy = dict(list(grupy.items())[:limit_groups])

    zapisane = 0
    bledy: list[str] = []
    log.info("Rekomendacje stawek: %s grup do odpytania", len(grupy))

    with AdsClient(profile_id=profile_id) as client:
        for i, ((camp, ag), pozycje) in enumerate(grupy.items(), 1):
            for partia in _chunks(pozycje, BATCH):
                wyrazenia = [{"type": typ, "value": tekst} for _, tekst, typ in partia]
                try:
                    wynik = _fetch_group(client, camp, ag, wyrazenia)
                except AdsApiError as exc:
                    bledy.append(f"grupa {ag}: {str(exc)[:120]}")
                    continue

                # Odpowiedz identyfikuje pozycje tekstem frazy, a my chcemy
                # zapisac ja pod identyfikatorem keyworda - stad mapowanie.
                po_tekscie = {tekst: kid for kid, tekst, _ in partia}
                dane = {}
                for tekst, wartosci in wynik.items():
                    kid = po_tekscie.get(tekst)
                    if not kid or len(wartosci) < 1:
                        continue
                    lo = wartosci[0]
                    hi = wartosci[-1]
                    med = wartosci[len(wartosci) // 2]
                    dane[kid] = (lo, med, hi)

                if dane:
                    with session_scope() as session:
                        zapisane += _store(
                            session, profile_id, "KEYWORD", dane, "CONVERSION_OPPORTUNITIES"
                        )

            if i % 25 == 0:
                log.info("  %s/%s grup, zapisano %s", i, len(grupy), zapisane)

    return {"groups": len(grupy), "stored": zapisane, "errors": bledy[:10],
            "error_count": len(bledy)}


def coverage(session: Session, profile_id: int | None = None) -> dict[str, Any]:
    """Ile encji ma rekomendacje i jak swieze."""
    from sqlalchemy import func

    conds = []
    if profile_id:
        conds.append(BidRecommendation.profile_id == profile_id)

    n, najstarsza, najnowsza = session.execute(
        select(
            func.count(), func.min(BidRecommendation.fetched_at),
            func.max(BidRecommendation.fetched_at),
        ).where(*conds)
    ).one()

    return {
        "count": n or 0,
        "oldest": najstarsza.isoformat() if najstarsza else None,
        "newest": najnowsza.isoformat() if najnowsza else None,
    }


def new_keyword_key(ad_group_id: str, match_type: str, text: str) -> str:
    """Klucz dla frazy, ktora jeszcze nie jest keywordem.

    Rekomendacja zalezy od grupy reklam i typu dopasowania, nie od samej frazy -
    ta sama fraza w innej grupie dostaje inna wycene. Stad klucz zlozony.
    """
    return f"{ad_group_id}|{match_type.upper()}|{text.strip().lower()}"


def sync_new_keywords(
    profile_id: int, kandydaci: list[dict[str, Any]], only_stale: bool = True
) -> dict[str, Any]:
    """Widelki dla fraz proponowanych do przejecia.

    kandydaci: [{campaign_id, ad_group_id, keyword_text, match_type}]

    Amazon wycenia fraze jeszcze nieistniejaca tak samo jak istniejaca - to ta
    sama operacja, ktorej uzywa jego wlasny panel przy dodawaniu slowa. Dzieki
    temu widac widelki, zanim keyword powstanie.
    """
    unikalne: dict[str, dict[str, Any]] = {}
    for k in kandydaci:
        ag = str(k.get("ad_group_id") or "")
        camp = str(k.get("campaign_id") or "")
        tekst = (k.get("keyword_text") or "").strip()
        match = (k.get("match_type") or "EXACT").upper()
        if not ag or not camp or not tekst or match not in MATCH_TO_EXPRESSION:
            continue
        unikalne[new_keyword_key(ag, match, tekst)] = {
            "campaign_id": camp, "ad_group_id": ag, "text": tekst, "match": match,
        }

    if only_stale and unikalne:
        prog = utcnow() - dt.timedelta(days=STALE_DAYS)
        with session_scope() as session:
            swieze = {
                r for r in session.execute(
                    select(BidRecommendation.entity_id).where(
                        BidRecommendation.profile_id == profile_id,
                        BidRecommendation.entity_type == "NEW_KEYWORD",
                        BidRecommendation.entity_id.in_(list(unikalne)),
                        BidRecommendation.fetched_at >= prog,
                    )
                ).scalars()
            }
        for k in swieze:
            unikalne.pop(k, None)

    if not unikalne:
        return {"groups": 0, "stored": 0, "errors": [], "error_count": 0}

    grupy: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    for klucz, opis in unikalne.items():
        grupy.setdefault((opis["campaign_id"], opis["ad_group_id"]), []).append((klucz, opis))

    zapisane = 0
    bledy: list[str] = []
    log.info("Widelki dla nowych fraz: %s grup, %s pozycji", len(grupy), len(unikalne))

    with AdsClient(profile_id=profile_id) as client:
        for (camp, ag), pozycje in grupy.items():
            for partia in _chunks(pozycje, BATCH):
                wyrazenia = [
                    {"type": MATCH_TO_EXPRESSION[o["match"]], "value": o["text"]}
                    for _, o in partia
                ]
                try:
                    wynik = _fetch_group(client, camp, ag, wyrazenia)
                except AdsApiError as exc:
                    bledy.append(f"grupa {ag}: {str(exc)[:120]}")
                    continue

                dane = {}
                for klucz, o in partia:
                    wartosci = wynik.get(o["text"])
                    if not wartosci:
                        continue
                    dane[klucz] = (wartosci[0], wartosci[len(wartosci) // 2], wartosci[-1])

                if dane:
                    with session_scope() as session:
                        zapisane += _store(
                            session, profile_id, "NEW_KEYWORD", dane,
                            "CONVERSION_OPPORTUNITIES",
                        )

    return {"groups": len(grupy), "stored": zapisane, "errors": bledy[:10],
            "error_count": len(bledy)}


def lookup(
    session: Session, profile_id: int, entity_type: str, ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Widelki dla podanych encji, gotowe do dolaczenia do wiersza."""
    if not ids:
        return {}
    rows = session.execute(
        select(BidRecommendation).where(
            BidRecommendation.profile_id == profile_id,
            BidRecommendation.entity_type == entity_type,
            BidRecommendation.entity_id.in_(ids),
        )
    ).scalars()
    return {
        r.entity_id: {
            "low": r.low, "median": r.median, "high": r.high,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        }
        for r in rows
    }

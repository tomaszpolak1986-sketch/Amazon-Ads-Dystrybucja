"""Zapis i odczyt sprzedazy calkowitej - mianownika do TACOS."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import and_, delete, func, literal, select
from sqlalchemy.orm import Session

from ..models import FxRate, Profile, TotalSalesDaily
from .zapis import wstaw_lub_zastap


def store(session: Session, rows: list[dict[str, Any]], replace_range: bool = True) -> dict[str, Any]:
    """Zapisuje wiersze sprzedazy calkowitej.

    Domyslnie czyscimy zakres dat, ktorego dotyczy import, i wstawiamy od nowa.
    Import tego samego okresu drugi raz ma go poprawic, a nie podwoic - a przy
    raporcie biznesowym poprawianie zdarza sie czesto, bo Amazon uzupelnia dane
    z kilkudniowym opoznieniem.
    """
    if not rows:
        return {"stored": 0, "replaced": 0, "start": None, "end": None}

    profile_ids = {r["profile_id"] for r in rows}
    daty = [r["date"] for r in rows]
    start, end = min(daty), max(daty)

    usuniete = 0
    if replace_range:
        for pid in profile_ids:
            wynik = session.execute(
                delete(TotalSalesDaily).where(
                    TotalSalesDaily.profile_id == pid,
                    TotalSalesDaily.date >= start,
                    TotalSalesDaily.date <= end,
                )
            )
            usuniete += wynik.rowcount or 0

    wstaw_lub_zastap(session, TotalSalesDaily, rows)
    return {
        "stored": len(rows),
        "replaced": usuniete,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def daily_totals(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date
) -> dict[dt.date, float]:
    """Sprzedaz calkowita per dzien.

    Wiersz zbiorczy (bez ASIN) ma pierwszenstwo przed suma pozycji: jest blizej
    prawdy niz suma tych ASIN-ow, ktore akurat znalazly sie w eksporcie. Gdy
    zbiorczego nie ma, sumujemy to, co jest.
    """
    conds = [TotalSalesDaily.date >= start, TotalSalesDaily.date <= end]
    if profile_id:
        conds.append(TotalSalesDaily.profile_id == profile_id)

    # Kwoty z rynkow w roznych walutach przeliczamy po kursie z dnia sprzedazy -
    # bez tego korony i zlotowki wchodzily do sumy jak euro.
    # Przy jednym rynku zostawiamy jego walute - przeliczamy tylko sumy zbiorcze.
    kurs = literal(1.0) if profile_id else func.coalesce(FxRate.rate_eur, 1.0)

    def suma(warunek):
        stmt = select(TotalSalesDaily.date, func.sum(TotalSalesDaily.sales * kurs))
        if not profile_id:
            stmt = stmt.join(
                Profile, Profile.profile_id == TotalSalesDaily.profile_id, isouter=True
            ).join(
                FxRate,
                and_(
                    FxRate.date == TotalSalesDaily.date,
                    FxRate.currency == Profile.currency_code,
                ),
                isouter=True,
            )
        return dict(
            session.execute(
                stmt.where(*conds, warunek).group_by(TotalSalesDaily.date)
            ).all()
        )

    zbiorcze = suma(TotalSalesDaily.asin.is_(None))
    per_asin = suma(TotalSalesDaily.asin.is_not(None))

    out: dict[dt.date, float] = dict(per_asin)
    out.update(zbiorcze)
    return {d: float(v or 0) for d, v in out.items()}


def total_for_range(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date
) -> float | None:
    """Suma sprzedazy calkowitej w okresie. None, gdy nie ma zadnych danych."""
    dzienne = daily_totals(session, profile_id, start, end)
    if not dzienne:
        return None
    return round(sum(dzienne.values()), 2)


def sales_by_asin(
    session: Session, profile_id: int | None, start: dt.date, end: dt.date
) -> dict[str, float]:
    conds = [
        TotalSalesDaily.date >= start,
        TotalSalesDaily.date <= end,
        TotalSalesDaily.asin.is_not(None),
    ]
    if profile_id:
        conds.append(TotalSalesDaily.profile_id == profile_id)

    rows = session.execute(
        select(TotalSalesDaily.asin, func.sum(TotalSalesDaily.sales))
        .where(*conds)
        .group_by(TotalSalesDaily.asin)
    ).all()
    return {a: float(v or 0) for a, v in rows if a}


def coverage(session: Session, profile_id: int | None = None) -> list[dict[str, Any]]:
    """Co juz mamy: zakres dat i liczba dni per rynek.

    Bez tego nie da sie odroznic TACOS policzonego z pelnego miesiaca od
    policzonego z trzech dni, ktore akurat ktos zaimportowal.
    """
    conds = []
    if profile_id:
        conds.append(TotalSalesDaily.profile_id == profile_id)

    rows = session.execute(
        select(
            TotalSalesDaily.profile_id,
            func.min(TotalSalesDaily.date),
            func.max(TotalSalesDaily.date),
            func.count(func.distinct(TotalSalesDaily.date)),
            func.sum(TotalSalesDaily.sales),
            func.count(func.distinct(TotalSalesDaily.asin)),
        )
        .where(*conds)
        .group_by(TotalSalesDaily.profile_id)
    ).all()

    return [
        {
            "profile_id": pid,
            "start": lo.isoformat() if lo else None,
            "end": hi.isoformat() if hi else None,
            "days": dni,
            "sales": round(float(suma or 0), 2),
            "asins": asiny,
        }
        for pid, lo, hi, dni, suma, asiny in rows
    ]

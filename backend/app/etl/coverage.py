"""Pokrycie danymi raportowymi - gdzie sa dziury i czym je zasypac.

Jedno miejsce prawdy dla przegladu (`cli.py coverage`) i dla dogrywki
(`backfill_gaps.py`). Wczesniej obie te rzeczy mialyby wlasna liste tabel
i wlasne progi, co konczy sie tym, ze przeglad pokazuje dziure, ktorej
dogrywka nie potrafi znalezc.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select

from ..db import session_scope
from ..models import (
    CampaignDaily,
    PlacementDaily,
    ProductDaily,
    Profile,
    SearchTermDaily,
    TargetDaily,
)

# Raport fraz ma krotsza retencje niz pozostale: Amazon oddaje mniej wiecej
# ostatnie dwa miesiace, reszta siega 95 dni. Sprawdzone na DE, UK i NL, ktore
# niezaleznie od siebie urwaly sie na tej samej dacie kalendarzowej - to
# granica po stronie Amazona, nie zgubione pobranie.
SEARCH_TERM_RETENTION = 65

# (nazwa po polsku, klucz raportu w REPORT_KINDS, model, retencja)
TABLES = [
    ("kampanie", "campaign", CampaignDaily, None),
    ("placementy", "placement", PlacementDaily, None),
    ("targety", "targeting", TargetDaily, None),
    ("frazy", "search_term", SearchTermDaily, SEARCH_TERM_RETENTION),
    ("produkty", "product", ProductDaily, None),
]


@dataclass
class Cell:
    label: str
    kind: str
    days: int
    first: dt.date | None
    last: dt.date | None
    expected: int

    @property
    def is_gap(self) -> bool:
        """Czy brak jest na tyle duzy, ze wyglada na zgubione pobranie.

        Tolerancja rosnie z oczekiwanym zakresem, bo dzien bez wydatku nie
        tworzy wiersza - a na malym rynku takich dni bywa kilka w miesiacu.
        Bez tego kazdy maly rynek zglaszalby dziure nie do zasypania.
        """
        return self.days < self.expected - max(3, self.expected * 0.10)


@dataclass
class MarketCoverage:
    profile_id: int
    country: str
    cells: list[Cell]

    @property
    def gaps(self) -> list[Cell]:
        return [c for c in self.cells if c.is_gap]


def survey(days: int = 95) -> tuple[dt.date, dt.date, list[MarketCoverage]]:
    """Pokrycie kazdego rynku, ktory ma jakiekolwiek dane."""
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)

    out: list[MarketCoverage] = []
    with session_scope() as session:
        profiles = session.execute(
            select(Profile.profile_id, Profile.country_code).order_by(Profile.country_code)
        ).all()

        for pid, cc in profiles:
            raw = []
            for label, kind, model, retencja in TABLES:
                n_days, lo, hi = session.execute(
                    select(
                        func.count(func.distinct(model.date)),
                        func.min(model.date), func.max(model.date),
                    ).where(
                        model.profile_id == pid,
                        model.date >= start, model.date <= end,
                    )
                ).one()
                raw.append((label, kind, n_days, lo, hi, retencja))

            if not any(n for _, _, n, _, _, _ in raw):
                continue

            # Za pelne uznajemy pokrycie rowne najlepszemu typowi na tym rynku;
            # typom z wlasna retencja przykladamy ich wlasna miare.
            best = max(n for _, _, n, _, _, _ in raw)
            cells = [
                Cell(label, kind, n, lo, hi, min(best, retencja) if retencja else best)
                for label, kind, n, lo, hi, retencja in raw
            ]
            out.append(MarketCoverage(pid, cc or str(pid), cells))

    return start, end, out

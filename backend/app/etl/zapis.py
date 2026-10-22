"""Zapis faktow odporny na duplikaty i na rownolegle przebiegi.

Zwykly INSERT po DELETE zakresu dziala tylko wtedy, gdy zapisy ida po kolei.
SQLite tak robi z natury (jeden pisarz naraz), ale Postgres pozwala dwom
transakcjom kasowac i wstawiac ten sam zakres jednoczesnie - i druga wpada
na klucz, ktory pierwsza wlasnie wstawila. Tak wygladal serwer przez dwa
tygodnie: raporty sie pobieraly, zapis odpadal na UniqueViolation, dane
staly w miejscu.

Blokada w `sync_lock` pilnuje, zeby przebiegi nie nakladaly sie w obrebie
jednego procesu. Ten modul zabezpiecza sam zapis: ON CONFLICT DO UPDATE
sprawia, ze nawet gdyby dwa zapisy jednak sie spotkaly (albo raport sam
zwrocil ten sam klucz dwa razy), wygrywa ostatni zamiast wywracac calosc.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

# Limit liczby parametrow w jednym zapytaniu: SQLite dopuszcza 32766
# (starsze wersje 999), Postgres 65535. Zamiast stalej liczby WIERSZY liczymy
# ja z liczby kolumn - tabela fraz ma ich 20, wiec 2000 wierszy to 40 tysiecy
# parametrow i zapis wywracal sie przy dluzszym oknie. Przy krotkim oknie
# partie byly mniejsze i problem nie wychodzil.
MAX_PARAMETROW = 20000
MAX_WIERSZY = 2000


def wstaw_lub_zastap(session: Session, model, rows: list[dict[str, Any]]) -> int:
    """INSERT ... ON CONFLICT (klucz glowny) DO UPDATE - Postgres i SQLite."""
    if not rows:
        return 0
    tabela = model.__table__
    klucz = _klucz_konfliktu(tabela)
    dialekt = session.get_bind().dialect.name
    insert = postgresql.insert if dialekt == "postgresql" else sqlite.insert

    # Klucze spoza tabeli odrzucamy po cichu - tak robil bulk_insert_mappings,
    # a budowniczowie raportow zostawiaja czasem pola pomocnicze.
    znane = set(tabela.c.keys())
    rows = [{k: v for k, v in r.items() if k in znane} for r in rows]

    # Postgres odrzuca ON CONFLICT, gdy ten sam klucz pojawia sie dwa razy
    # w jednym INSERT ("cannot affect row a second time"). Budowniczowie
    # raportow scalaja duplikaty, ale to ostatnia linia obrony: zostaje
    # ostatni wiersz z danym kluczem.
    if klucz:
        unikalne: dict[tuple, dict[str, Any]] = {}
        for r in rows:
            unikalne[tuple(r.get(k) for k in klucz)] = r
        rows = list(unikalne.values())

    porcja_wierszy = max(1, min(MAX_WIERSZY, MAX_PARAMETROW // max(1, len(tabela.c))))
    for i in range(0, len(rows), porcja_wierszy):
        porcja = rows[i : i + porcja_wierszy]
        # Aktualizujemy tylko kolumny obecne w wierszach - reszta (np. domyslne
        # znaczniki czasu) zostaje jak byla.
        stmt = insert(tabela).values(porcja)
        if klucz is None:
            # Tabela bez naturalnego klucza (sam autoinkrement): nie ma po czym
            # rozpoznac duplikatu, wiec zostaje zwykly insert po DELETE zakresu.
            session.execute(stmt)
            continue
        kolumny = [k for k in porcja[0] if k not in klucz]
        if kolumny:
            stmt = stmt.on_conflict_do_update(
                index_elements=klucz,
                set_={k: getattr(stmt.excluded, k) for k in kolumny},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=klucz)
        session.execute(stmt)
    return len(rows)


def _klucz_konfliktu(tabela) -> list[str] | None:
    """Po czym poznac ten sam fakt: klucz glowny, a przy sztucznym `id` -
    pierwsze ograniczenie unikalne (np. date+profile_id+channel)."""
    pk = list(tabela.primary_key.columns)
    sztuczny = (
        len(pk) == 1
        and pk[0].autoincrement in (True, "auto")
        and pk[0].type.python_type is int
    )
    if not sztuczny:
        return [c.name for c in pk]
    for c in tabela.constraints:
        if isinstance(c, UniqueConstraint) and len(c.columns):
            return [k.name for k in c.columns]
    return None

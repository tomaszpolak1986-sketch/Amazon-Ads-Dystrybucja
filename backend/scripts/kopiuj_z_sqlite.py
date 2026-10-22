"""Kopiuje cala baze z migawki SQLite do Postgresa wskazanego w DATABASE_URL.

    python backend/scripts/kopiuj_z_sqlite.py <migawka.db> [--wyczysc]

Idzie po tabelach w kolejnosci kluczy obcych (rodzice przed dziecmi),
przez te same modele po obu stronach - wiec JSON, daty i booleany przechodza
przez konwersje typow SQLAlchemy, a nie jako surowy tekst z SQLite.

Bez --wyczysc odmawia pracy na niepustej bazie. Z --wyczysc najpierw oproznia
wszystkie tabele (dzieci przed rodzicami) - w tej samej transakcji, wiec
przerwanie w polowie zostawia stara zawartosc nietknieta.
"""
import os, sys, time
from pathlib import Path

# backend/scripts/ -> backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, select, text, func
from app.models import Base
from app.config import settings

if len(sys.argv) < 2:
    sys.exit(__doc__)
migawka = Path(sys.argv[1]).resolve()
wyczysc = "--wyczysc" in sys.argv

zrodlo = create_engine(f"sqlite:///{migawka.as_posix()}")
cel = create_engine(settings.resolved_database_url())
Base.metadata.create_all(cel)

PORCJA = 5000
with zrodlo.connect() as z, cel.begin() as c:
    if wyczysc:
        for t in reversed(Base.metadata.sorted_tables):
            c.execute(t.delete())
        print("cel oprozniony", flush=True)
    for t in Base.metadata.sorted_tables:
        n = c.execute(select(func.count()).select_from(t)).scalar()
        if n:
            sys.exit(f"cel nie jest pusty: {t.name} ma {n} wierszy - uzyj --wyczysc")
    for t in Base.metadata.sorted_tables:
        t0 = time.time(); ile = 0
        cols = [col.name for col in t.columns]
        wynik = z.execute(select(t))
        while True:
            paczka = wynik.fetchmany(PORCJA)
            if not paczka:
                break
            c.execute(t.insert(), [dict(zip(cols, r)) for r in paczka])
            ile += len(paczka)
        # Sekwencje: po wstawieniu jawnych id trzeba je przesunac za max
        for col in t.primary_key.columns:
            if len(t.primary_key.columns) == 1 and col.type.python_type is int and col.autoincrement != False:
                seq = c.execute(text("select pg_get_serial_sequence(:t, :c)"), {"t": t.name, "c": col.name}).scalar()
                if seq:
                    c.execute(text(f"select setval('{seq}', coalesce((select max({col.name}) from {t.name}), 0) + 1, false)"))
        print(f"{t.name:28s} {ile:8d}  {time.time()-t0:5.1f}s", flush=True)
print("GOTOWE")

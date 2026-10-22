"""Jedna synchronizacja naraz.

Zrodel przebiegow jest kilka: przycisk w panelu, odswiezanie planowe,
nadrabianie po starcie, sprawdzenie swiezosci co godzine. Kazde z nich
kasuje i wstawia te same zakresy dat, wiec dwa naraz to gwarantowany
konflikt zapisu w Postgresie (patrz `zapis.py`) i podwojne zuzycie limitu
Amazona. Blokada jest procesowa - wystarcza, bo wszystkie przebiegi zyja
w jednym procesie backendu.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_LOCK = threading.Lock()
_CO_TRWA: str | None = None


def co_trwa() -> str | None:
    """Nazwa trwajacego przebiegu albo None."""
    return _CO_TRWA


def zajmij(nazwa: str) -> None:
    """Zajmuje blokade bez czekania. Rzuca `Zajete`, gdy inny przebieg trwa."""
    global _CO_TRWA
    if not _LOCK.acquire(blocking=False):
        raise Zajete(_CO_TRWA or "synchronizacja")
    _CO_TRWA = nazwa


def zwolnij() -> None:
    global _CO_TRWA
    _CO_TRWA = None
    _LOCK.release()


@contextmanager
def przebieg(nazwa: str):
    """Zajmuje blokade na czas przebiegu. Rzuca `Zajete`, gdy inny trwa.

    Nie czekamy - przebieg, ktory nie dostal blokady, po prostu odpuszcza.
    Dla planowych to bez straty (nastepny termin przyjdzie), a dla przycisku
    lepsza jest natychmiastowa odpowiedz "juz trwa" niz kolejka klikniec.
    """
    zajmij(nazwa)
    try:
        yield
    finally:
        zwolnij()


class Zajete(RuntimeError):
    def __init__(self, nazwa: str):
        super().__init__(f"Synchronizacja już trwa ({nazwa})")
        self.nazwa = nazwa

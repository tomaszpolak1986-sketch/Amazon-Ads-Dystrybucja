"""Klucz licencyjny panelu.

Klucz to `<tresc>.<podpis>` w base64url: tresc to JSON z nazwa klienta i data
waznosci, podpis to Ed25519 autora panelu. Tutaj lezy wylacznie klucz
PUBLICZNY - pozwala sprawdzic podpis, ale nie wystawic nowej licencji, wiec
jego obecnosc w obrazie niczego nie zdradza.

Wydawanie: narzedzia/licencja.py (poza obrazem, u autora).

Opcjonalne pole "konta" przypina licencje do kont reklamowych Amazon klienta
(accountInfo.id profilu - dla sprzedawcy to jego Merchant ID). Bez niego
licencja dziala z kazdym kontem; z nim panel nie wpusci profili innej firmy,
wiec skopiowana instalacja nie przyda sie nikomu poza wlascicielem licencji.

Sprawdzamy przy kazdym zapytaniu, nie tylko przy starcie: kontener chodzi
miesiacami bez restartu, a licencja ma wygasac w swoim dniu, a nie przy
najblizszej aktualizacji. Sam podpis weryfikujemy raz na klucz (cache), przy
kolejnych zapytaniach porownujemy juz tylko date.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from dataclasses import asdict, dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .config import settings

KLUCZ_PUBLICZNY = "2Cy1MdHQATnkXMrT7FSLHTORZmmPlu0JOHRyHaB4Brc"


@dataclass(frozen=True)
class Stan:
    ok: bool
    powod: str
    klient: str | None = None
    wazna_do: str | None = None
    # None = licencja bez przypiecia do kont Amazon
    konta: tuple[str, ...] | None = None

    def jako_slownik(self) -> dict:
        # Identyfikatory kont nie wychodza poza serwer - /api/health jest publiczny
        d = asdict(self)
        d["przypisana_do_kont"] = d.pop("konta") is not None
        return d


def _z_b64(tekst: str) -> bytes:
    return base64.urlsafe_b64decode(tekst + "=" * (-len(tekst) % 4))


@lru_cache(maxsize=4)
def _odczytaj(klucz: str) -> tuple[str, dt.date, tuple[str, ...] | None] | str:
    """(klient, wazna_do, konta) albo opis bledu. Bez daty dzisiejszej - to idzie do cache."""
    try:
        tresc_b64, podpis_b64 = klucz.strip().split(".")
        tresc, podpis = _z_b64(tresc_b64), _z_b64(podpis_b64)
    except (ValueError, binascii.Error):
        return "Klucz licencyjny ma nieprawidlowy format."

    try:
        Ed25519PublicKey.from_public_bytes(_z_b64(KLUCZ_PUBLICZNY)).verify(podpis, tresc)
    except InvalidSignature:
        return "Klucz licencyjny jest nieprawidlowy."

    try:
        dane = json.loads(tresc)
        konta = dane.get("konta")
        if konta is not None:
            if not isinstance(konta, list) or not konta:
                return "Klucz licencyjny jest uszkodzony."
            konta = tuple(str(k) for k in konta)
        return dane["klient"], dt.date.fromisoformat(dane["wazna_do"]), konta
    except (ValueError, KeyError, TypeError):
        return "Klucz licencyjny jest uszkodzony."


def sprawdz(klucz: str, dzis: dt.date | None = None) -> Stan:
    if not klucz.strip():
        return Stan(False, "Brak klucza licencyjnego (LICENCJA w pliku konfiguracyjnym).")

    wynik = _odczytaj(klucz)
    if isinstance(wynik, str):
        return Stan(False, wynik)

    klient, wazna_do, konta = wynik
    dzis = dzis or dt.date.today()
    if dzis > wazna_do:
        return Stan(False, f"Licencja wygasla {wazna_do.isoformat()}.", klient, wazna_do.isoformat(), konta)
    return Stan(True, "Licencja wazna.", klient, wazna_do.isoformat(), konta)


def stan() -> Stan:
    return sprawdz(settings.licencja)


def dozwolone_konta() -> frozenset[str] | None:
    """Konta Amazon, z ktorymi wolno pracowac; None = bez ograniczen.

    Lokalnie (bez wymogu licencji) zawsze None. Import znacznika tutaj, a nie
    na gorze, bo testy podmieniaja go w module main.
    """
    from ._wydanie import WYMAGA_LICENCJI

    if not WYMAGA_LICENCJI:
        return None
    s = stan()
    return frozenset(s.konta) if s.ok and s.konta else None

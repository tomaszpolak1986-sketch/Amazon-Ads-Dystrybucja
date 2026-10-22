"""Jednolita obsluga czasu.

W bazie trzymamy naiwne znaczniki w UTC. SQLite nie przechowuje strefy, a
mieszanie `func.now()` (UTC po stronie silnika) z `datetime.now()` (czas lokalny
procesu) dawalo znaczniki rozjezdzone o offset strefy - plan z 15-minutowym TTL
wygladal, jakby wygasal dwie godziny po utworzeniu.

Do JSON-a serializujemy z jawnym sufiksem "Z", zeby przegladarka przeliczyla
czas na strefe uzytkownika zamiast zakladac, ze to czas lokalny.
"""
from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    """Naiwny znacznik w UTC - taki, jaki trzymamy w kolumnach DateTime."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def iso_utc(value: dt.datetime | None) -> str | None:
    """ISO 8601 z sufiksem Z. None przechodzi bez zmian."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value.isoformat() + "Z"

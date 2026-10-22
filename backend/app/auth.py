"""Uwierzytelnianie panelu.

Jeden uzytkownik, haslo trzymane wylacznie jako skrot argon2 w zmiennej
srodowiskowej. Sesja to podpisane ciasteczko HttpOnly - serwer nie przechowuje
stanu sesji, wiec restart kontenera nikogo nie wylogowuje, a wygasniecie
pilnuje sam podpis.

Chronimy wszystko pod /api poza logowaniem i /api/health. Health musi byc
otwarty, bo sluzy Dockerowi do sprawdzania, czy kontener zyje.
"""
from __future__ import annotations

import logging
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

log = logging.getLogger(__name__)

COOKIE_NAME = "adspanel_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # dwa tygodnie

# Sciezki dostepne bez zalogowania
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/me"}
PUBLIC_PREFIXES = ("/docs", "/redoc", "/openapi.json")

_hasher = PasswordHasher()


class AuthError(RuntimeError):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _serializer() -> URLSafeTimedSerializer:
    if not settings.auth_secret:
        raise AuthError(
            "Brak AUTH_SECRET w .env. Wygeneruj go:\n"
            "  python backend/scripts/cli.py setpassword"
        )
    return URLSafeTimedSerializer(settings.auth_secret, salt="adspanel-session")


def verify_password(password: str) -> bool:
    """Porownanie w stalym czasie - argon2 zapewnia je samo."""
    if not settings.auth_password_hash:
        raise AuthError(
            "Panel nie ma ustawionego hasla. Ustaw je:\n"
            "  python backend/scripts/cli.py setpassword"
        )
    try:
        _hasher.verify(settings.auth_password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except InvalidHashError as exc:
        raise AuthError(f"AUTH_PASSWORD_HASH jest uszkodzony: {exc}") from exc


def issue_session(response: Response) -> None:
    token = _serializer().dumps({"v": 1})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        # Secure tylko za TLS - inaczej przegladarka odrzucilaby ciasteczko
        # przy dostepie po http w sieci lokalnej
        secure=settings.auth_cookie_secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except SignatureExpired:
        return False
    except (BadSignature, AuthError):
        return False


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def require_auth(request: Request) -> None:
    """Zaleznosc FastAPI - podnosi 401 dla niezalogowanych."""
    if settings.auth_disabled:
        return
    if not is_authenticated(request):
        raise HTTPException(401, "Wymagane zalogowanie")


def generate_secret() -> str:
    return secrets.token_urlsafe(48)

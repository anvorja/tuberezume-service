"""Contraseñas (scrypt, solo biblioteca estándar) y tokens de sesión (JWT firmado)."""

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta, timezone

import jwt

from .config import get_settings

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, h_b64 = stored.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(h_b64)
    except ValueError:
        return False
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return hmac.compare_digest(h, expected)


# Hash de relleno: así el login tarda lo mismo exista o no el usuario.
_DUMMY = hash_password("relleno-sin-uso")


def burn_time(password: str) -> None:
    verify_password(password, _DUMMY)


def create_token(user_id: str) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(days=s.token_days)
    return jwt.encode({"sub": user_id, "exp": exp}, s.jwt_secret, algorithm="HS256")


def read_token(token: str) -> str | None:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        return None


# Freno a fuerza bruta: 5 fallos seguidos bloquean 60 s esa combinación usuario+IP.
_fails: dict[str, list[float]] = {}
_MAX, _WINDOW = 5, 60.0


def locked(key: str) -> bool:
    now = time.monotonic()
    _fails[key] = [t for t in _fails.get(key, []) if now - t < _WINDOW]
    return len(_fails[key]) >= _MAX


def register_fail(key: str) -> None:
    _fails.setdefault(key, []).append(time.monotonic())


def clear_fails(key: str) -> None:
    _fails.pop(key, None)

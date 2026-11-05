"""密码哈希（PBKDF2，无需额外依赖）与 JWT 令牌。"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

import jwt

from .config import settings

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(expected, digest)
    except Exception:
        return False


def create_access_token(subject: str, role: str, display_name: str = "") -> str:
    expire = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "role": role, "name": display_name, "exp": expire, "iat": dt.datetime.now(dt.timezone.utc)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])

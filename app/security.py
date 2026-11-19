import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import get_settings


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode()[:72], h.encode())
    except ValueError:
        return False


def password_temporal() -> str:
    return secrets.token_urlsafe(9)


def crear_token(sub: str, rol: str, empresa_id: str | None, extra: dict | None = None) -> str:
    s = get_settings()
    payload = {
        "sub": sub,
        "rol": rol,
        "emp": empresa_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=s.jwt_expire_min),
        **(extra or {}),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def leer_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])

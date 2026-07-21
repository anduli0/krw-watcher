"""JWT issue/verify (HS256). PyJWT — pure-python, no native crypto build needed."""
from datetime import datetime, timedelta, timezone
import jwt
from backend.config import settings

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 20


def create_token(payload: dict) -> str:
    data = payload.copy()
    data["exp"] = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(data, settings.JWT_SECRET, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    if not token or not settings.JWT_SECRET:
        return {}
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    except Exception:
        return {}

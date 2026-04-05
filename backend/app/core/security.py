import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

# bcrypt не принимает пароль > 72 байт; обрезаем вручную и отключаем ошибку от passlib
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=False)

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-use-env")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 дней


def _truncate_to_72_bytes(s: str) -> str:
    """bcrypt принимает не более 72 байт; обрезаем по байтам (кириллица = 2 байта на символ)."""
    if not s:
        return ""
    s = s if isinstance(s, str) else str(s)
    raw = s.encode("utf-8")
    if len(raw) <= 72:
        return s
    return raw[:72].decode("utf-8", errors="ignore")


def hash_password(password: str) -> str:
    password = _truncate_to_72_bytes(password)
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    plain = _truncate_to_72_bytes(plain)
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

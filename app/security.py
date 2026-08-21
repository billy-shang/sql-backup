"""密码哈希、JWT、连接凭据加解密。"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.config import JWT_EXPIRE_HOURS, SECRET_FILE, ensure_dirs

log = logging.getLogger(__name__)
ALGORITHM = "HS256"


def _load_or_create_key() -> bytes:
    ensure_dirs()
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    SECRET_FILE.write_bytes(key)
    log.info("[security] 已生成密钥文件 %s", SECRET_FILE)
    return key


_KEY = _load_or_create_key()
_FERNET = Fernet(_KEY)
_JWT_SECRET = hashlib.sha256(_KEY).hexdigest()


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), 180000).hex()
    return f"{salt}${digest}"


def verify_password(plain: str, stored: str) -> bool:
    try:
        salt, digest = (stored or "").split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), 180000).hex()
    return secrets.compare_digest(check, digest)


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _FERNET.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _FERNET.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        log.warning("[security] 解密失败: %s", e)
        return ""


def create_token(sub: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload: dict[str, Any] = {"sub": sub, "role": role, "exp": exp}
    return jwt.encode(payload, _JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError as e:
        log.info("[security] token 无效: %s", e)
        raise

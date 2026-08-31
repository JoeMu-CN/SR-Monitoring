"""密码哈希与强度策略。"""

from typing import Final

import bcrypt
from fastapi import HTTPException, status

_BCRYPT_MAX_PASSWORD_BYTES: Final = 72
_WEAK_PASSWORDS = {
    "password",
    "12345678",
    "abcdefgh",
    "qwerty12",
    "admin123",
    "letmein1",
    "welcome1",
    "password1",
    "123456789",
    "changeme1",
}


def hash_password(password: str) -> str:
    hashed: bytes = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bool(
            bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        )
    except (ValueError, TypeError):
        return False


def validate_password_strength(
    password: str, *, status_code: int = status.HTTP_400_BAD_REQUEST
) -> None:
    """弱口令与基本强度校验，默认保持既有 400 语义。"""
    if len(password) < 8:
        raise HTTPException(status_code=status_code, detail="密码长度至少 8 位")
    if len(password.encode("utf-8")) > _BCRYPT_MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=status_code,
            detail="密码 UTF-8 编码后不得超过 72 字节",
        )
    if password.strip().lower() in _WEAK_PASSWORDS:
        raise HTTPException(status_code=status_code, detail="密码过于常见，请更换")
    has_letter = any(character.isalpha() for character in password)
    has_digit = any(character.isdigit() for character in password)
    if not (has_letter and has_digit):
        raise HTTPException(
            status_code=status_code,
            detail="密码需同时包含字母和数字",
        )

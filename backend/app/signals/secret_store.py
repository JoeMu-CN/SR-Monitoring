"""数据源控制台运行密钥的加密存储。

控制台保存的 API Key（如天眼查 MCP Key）以 Fernet 对称加密的密文落入
``data_sources.api_key_encrypted``，运行时（天眼查网关等）按需解密使用，
数据库不落明文，接口也不回传明文。

加密密钥来源（优先级从高到低）：
1. ``DATA_SOURCE_SECRET_KEY`` 环境变量：Fernet base64 密钥，生产环境必须配置；
2. 缺省时由 ``DATABASE_URL`` 派生：同部署实例内可解密，切换数据库实例后
   旧密文失效（需在控制台重新配置密钥）。该模式仅建议开发/内部环境使用。
"""

import base64
import hashlib
import logging
import os
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from app import config

logger = logging.getLogger(__name__)

_PREFIX: Final[str] = "supplier-risk-console:v1"
_DERIVE_SALT: Final[str] = "supplier-risk-monitoring/secret-store"


def _derive_key() -> bytes:
    """由 DATABASE_URL 派生的部署内稳定密钥（非安全密钥，仅兜底）。"""
    digest = hashlib.sha256(
        f"{_PREFIX}:{config.DATABASE_URL}:{_DERIVE_SALT}".encode()
    ).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    configured = os.getenv("DATA_SOURCE_SECRET_KEY", "").strip()
    if not configured:
        logger.warning(
            "未配置 DATA_SOURCE_SECRET_KEY，运行密钥使用 DATABASE_URL 派生密钥加密；"
            "生产环境请配置 Fernet 密钥"
        )
        return Fernet(_derive_key())
    try:
        return Fernet(configured.encode("ascii"))
    except (ValueError, TypeError):
        raise RuntimeError(
            "DATA_SOURCE_SECRET_KEY 不是合法的 Fernet 密钥，"
            "请使用 `python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` 生成"
        ) from None


def encrypt_secret(plaintext: str) -> str:
    """加密明文密钥，返回 ASCII 密文。"""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str | None:
    """解密密文密钥；密文无效或密钥不可用时返回 None。"""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None

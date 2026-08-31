"""会话令牌派生与请求信息脱敏。"""

import hashlib
import hmac
import secrets

from app.config import SESSION_SECRET


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_token_hash(token: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def csrf_token_for_session(token: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        f"csrf:{token}".encode(),
        hashlib.sha256,
    ).hexdigest()


def mask_ip(ip: str | None) -> str | None:
    """IP 脱敏：IPv4 末段置 0，IPv6 取前 4 段后截断。"""
    if not ip:
        return None
    ip = ip.strip()
    if "." in ip:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
    if ":" in ip:
        parts = ip.split(":")
        if len(parts) >= 4:
            return ":".join(parts[:4]) + "::"
    return "unknown"


def mask_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()[:16]

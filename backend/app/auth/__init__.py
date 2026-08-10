"""认证模块：本地账号 + 密码（bcrypt）+ 服务端会话。"""

from app.auth.models import ROLE_CHOICES, STATUS_CHOICES, AuthSession, SecurityAuditEvent, User
from app.auth.security import (
    CurrentUser,
    get_current_user,
    require_permission,
    role_permissions,
    write_audit,
)

__all__ = [
    "User",
    "AuthSession",
    "SecurityAuditEvent",
    "ROLE_CHOICES",
    "STATUS_CHOICES",
    "CurrentUser",
    "get_current_user",
    "require_permission",
    "role_permissions",
    "write_audit",
]

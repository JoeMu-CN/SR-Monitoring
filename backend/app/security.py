"""最小角色边界。

当前项目尚未接入统一身份认证，先由网关注入 ``X-User-Role``。
正式部署必须由 SSO/API 网关签发并覆盖该请求头，不能信任浏览器自行伪造。
"""

from typing import Annotated

from fastapi import Header, HTTPException, status


def require_admin(
    role: Annotated[str | None, Header(alias="X-User-Role")] = None,
) -> str:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可以修改配置",
        )
    return role

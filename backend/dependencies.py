# backend/dependencies.py
# 无 DB / 无 JWT 的轻量身份识别：从请求头读取客户 ID，演示环境用默认值。

from typing import Optional

from fastapi import Depends, Header, HTTPException

DEFAULT_CUSTOMER_ID = "demo-customer"
DEFAULT_TENANT_ID = "tenant_default"


async def get_current_user(
    x_customer_id: Optional[str] = Header(default=None, description="客户 ID（演示环境可省略）"),
    x_role: Optional[str] = Header(default=None, description="角色（customer/agent）"),
) -> dict:
    """FastAPI 依赖：返回当前用户信息，保持各路由 Depends(get_current_user) 签名一致。

    生产环境可替换为 JWT / OAuth 解析，返回结构不变：
    {"user_id", "role", "tenant_id"}
    """
    return {
        "user_id": x_customer_id or DEFAULT_CUSTOMER_ID,
        "role": x_role or "customer",
        "tenant_id": DEFAULT_TENANT_ID,
    }


async def require_agent(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """坐席权限校验：审批、接管等人工接口只能由 role=agent 调用。

    注意：演示环境仍用 X-Role 头模拟身份，生产必须替换为 JWT / OAuth，
    并从 token 中解析角色，不能信任客户端自报的 header。
    """
    if current_user.get("role") != "agent":
        raise HTTPException(status_code=403, detail="需要人工坐席权限")
    return current_user

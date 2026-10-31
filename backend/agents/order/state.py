# backend/agents/order/state.py

from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class OrderState(TypedDict):
    """订单状态 Agent 的完整状态定义（多轮，槽位由 MemorySaver 跨轮恢复）。"""

    messages: Annotated[list[BaseMessage], add_messages]

    # ── 请求上下文 ─────────────────────────────────────────────
    customer_id: str
    tenant_id:   str
    session_id:  str

    # ── 已收集槽位 ─────────────────────────────────────────────
    order_no: Optional[str]   # 订单号
    phone:    Optional[str]   # 手机号

    # ── 订单查询结果 ───────────────────────────────────────────
    order_info: Optional[dict]  # OrderInfo 转 dict
    not_found:  bool            # 订单号查不到

    # ── 回复与结果 ─────────────────────────────────────────────
    reply:             str
    structured_output: Optional[dict]
    fallback_used:     bool

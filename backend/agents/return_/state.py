# backend/agents/return_/state.py

from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class ReturnState(TypedDict):
    """退换货流程 Agent 的完整状态定义（HitL）。"""

    messages: Annotated[list[BaseMessage], add_messages]

    # ── 请求上下文 ─────────────────────────────────────────────
    customer_id: str
    tenant_id:   str
    session_id:  str

    # ── 申请信息 ───────────────────────────────────────────────
    application_id: str
    order_no:       str
    reason:         str

    # ── 订单信息（rule_audit 节点查询订单系统补全）───────────────
    product_name:    str
    amount:          float
    is_used:         bool
    is_in_warranty:  bool

    # ── 规则审核结果 ───────────────────────────────────────────
    rule_audit: Optional[dict]   # {auto_approved, needs_review, reason}

    # ── Human-in-the-Loop ─────────────────────────────────────
    decision: Optional[dict]     # 人工决策（interrupt 恢复时传入）

    # ── 最终结果 ───────────────────────────────────────────────
    final_result:      Optional[dict]
    structured_output: Optional[dict]
    fallback_used:     bool

# backend/agents/complaint/state.py

from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class ComplaintState(TypedDict):
    """投诉升级 Agent 的完整状态定义（HitL 安全兜底）。"""

    messages: Annotated[list[BaseMessage], add_messages]

    # ── 请求上下文 ─────────────────────────────────────────────
    customer_id: str
    tenant_id:   str
    session_id:  str

    # ── 情绪识别结果 ───────────────────────────────────────────
    risk_level: str          # normal / high
    reason:     str          # 判定依据

    # ── 回复与转人工 ───────────────────────────────────────────
    reply:             str
    escalated:         bool
    escalation:        Optional[dict]
    structured_output: Optional[dict]
    fallback_used:     bool

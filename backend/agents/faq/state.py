# backend/agents/faq/state.py

from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class FAQState(TypedDict):
    """FAQ 问答 Agent 的完整状态定义。"""

    # ── 消息历史（add_messages reducer）───────────────────────
    messages: Annotated[list[BaseMessage], add_messages]

    # ── 请求上下文（Orchestrator / API 注入）────────────────────
    customer_id: str            # 客户 ID
    tenant_id:   str            # 租户 ID
    session_id:  str            # 会话 ID

    # ── Query 与检索结果 ──────────────────────────────────────
    original_query:    str         # 用户原始输入
    ranked_chunks:     list[dict]  # 检索 + 精排后的 Top-K chunk
    confidence:        float       # 检索置信度 [0, 1]
    is_high_confidence: bool       # confidence >= 阈值

    # ── 生成结果 ──────────────────────────────────────────────
    answer:            str         # 最终回答文本
    sources:           list[str]   # 引用来源列表
    answer_mode:       str         # "rag" / "human_handoff"
    structured_output: Optional[dict]
    fallback_used:     bool

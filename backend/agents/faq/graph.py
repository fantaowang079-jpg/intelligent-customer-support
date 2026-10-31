# backend/agents/faq/graph.py

from langgraph.graph import StateGraph, START, END

from backend.agents.faq.state import FAQState
from backend.agents.faq.nodes import (
    retrieve_node,
    generate_rag_node,
    escalate_human_node,
    _route_by_confidence,
)
from backend.core.memory import get_memory_saver


def build_faq_graph():
    """构建 FAQ 问答 Agent 图：检索 → 置信度路由 → {RAG 生成 | 转人工}。"""
    builder = StateGraph(FAQState)

    builder.add_node("retrieve",       retrieve_node)
    builder.add_node("generate_rag",   generate_rag_node)
    builder.add_node("escalate_human", escalate_human_node)

    builder.add_edge(START, "retrieve")
    builder.add_conditional_edges(
        "retrieve",
        _route_by_confidence,
        {
            "high": "generate_rag",
            "low":  "escalate_human",
        },
    )
    builder.add_edge("generate_rag",   END)
    builder.add_edge("escalate_human", END)

    checkpointer = get_memory_saver("faq")
    return builder.compile(checkpointer=checkpointer)

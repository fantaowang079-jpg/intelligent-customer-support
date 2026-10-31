# backend/agents/return_/graph.py

from langgraph.graph import StateGraph, START, END

from backend.agents.return_.state import ReturnState
from backend.agents.return_.nodes import (
    rule_audit_node,
    manual_review_node,
    apply_decision_node,
    execute_node,
    _route_by_audit,
)
from backend.core.memory import get_memory_saver


def build_return_graph():
    """构建退换货流程 Agent 图（HitL）：
    rule_audit → {自动 → apply_decision | 人工 → manual_review[interrupt] → apply_decision}
    → execute → END
    """
    builder = StateGraph(ReturnState)

    builder.add_node("rule_audit",     rule_audit_node)
    builder.add_node("manual_review",  manual_review_node)
    builder.add_node("apply_decision", apply_decision_node)
    builder.add_node("execute",        execute_node)

    builder.add_edge(START, "rule_audit")
    builder.add_conditional_edges(
        "rule_audit",
        _route_by_audit,
        {
            "auto":   "apply_decision",
            "manual": "manual_review",
        },
    )
    builder.add_edge("manual_review",  "apply_decision")
    builder.add_edge("apply_decision", "execute")
    builder.add_edge("execute",        END)

    checkpointer = get_memory_saver("return")
    return builder.compile(checkpointer=checkpointer)

# backend/agents/order/graph.py

from langgraph.graph import StateGraph, START, END

from backend.agents.order.state import OrderState
from backend.agents.order.nodes import (
    extract_slots_node,
    ask_missing_node,
    lookup_order_node,
    not_found_node,
    report_node,
    _route_by_slots,
    _route_after_lookup,
)
from backend.core.memory import get_memory_saver


def build_order_graph():
    """构建订单状态 Agent 图（多轮状态机）：
    extract_slots → {追问缺失 | 查询订单} → {订单不存在 | 播报状态}
    """
    builder = StateGraph(OrderState)

    builder.add_node("extract_slots", extract_slots_node)
    builder.add_node("ask_missing",   ask_missing_node)
    builder.add_node("lookup_order",  lookup_order_node)
    builder.add_node("not_found",     not_found_node)
    builder.add_node("report",        report_node)

    builder.add_edge(START, "extract_slots")
    builder.add_conditional_edges(
        "extract_slots",
        _route_by_slots,
        {
            "ask_missing": "ask_missing",
            "lookup":      "lookup_order",
        },
    )
    builder.add_edge("ask_missing", END)
    builder.add_conditional_edges(
        "lookup_order",
        _route_after_lookup,
        {
            "not_found": "not_found",
            "report":    "report",
        },
    )
    builder.add_edge("not_found", END)
    builder.add_edge("report",    END)

    checkpointer = get_memory_saver("order")
    return builder.compile(checkpointer=checkpointer)

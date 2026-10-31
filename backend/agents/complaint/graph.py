# backend/agents/complaint/graph.py

from langgraph.graph import StateGraph, START, END

from backend.agents.complaint.state import ComplaintState
from backend.agents.complaint.nodes import (
    emotion_detect_node,
    comfort_node,
    escalate_node,
    _route_by_risk,
)
from backend.core.memory import get_memory_saver


def build_complaint_graph():
    """构建投诉升级 Agent 图（HitL 安全兜底）：
    emotion_detect → {安抚 | 高危 interrupt 转人工}
    """
    builder = StateGraph(ComplaintState)

    builder.add_node("emotion_detect", emotion_detect_node)
    builder.add_node("comfort",        comfort_node)
    builder.add_node("escalate",       escalate_node)

    builder.add_edge(START, "emotion_detect")
    builder.add_conditional_edges(
        "emotion_detect",
        _route_by_risk,
        {
            "comfort":  "comfort",
            "escalate": "escalate",
        },
    )
    builder.add_edge("comfort",  END)
    builder.add_edge("escalate", END)

    checkpointer = get_memory_saver("complaint")
    return builder.compile(checkpointer=checkpointer)

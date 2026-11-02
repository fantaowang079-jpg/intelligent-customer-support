# tests/test_complaint_escalation.py
# P0 回归：高危投诉必须真正停在 interrupt，转人工记录不能是「假 pending」。

import asyncio

from langchain_core.messages import AIMessage

from backend.api.v1 import complaint as complaint_api
from backend.agents.complaint import nodes as complaint_nodes


def _run_escalation(customer_id: str, message: str, risk_level: str) -> str:
    async def _inner() -> str:
        escalation_id = complaint_api.start_escalation(
            customer_id, "tenant_default", message, risk_level, "test reason",
        )
        await asyncio.gather(*list(complaint_api._background_tasks))
        return escalation_id

    return asyncio.run(_inner())


def test_high_risk_message_really_interrupts():
    escalation_id = _run_escalation("customer-high", "再不解决我就去 12315 投诉你们", "high")
    record = complaint_api._escalations[escalation_id]

    # 真正停在 interrupt 才算待接管
    assert record["status"] == "pending"


def test_router_high_urgency_is_not_downgraded_by_text_rules():
    # 文本本身不含高危词，但路由侧已判定高危：不能被情绪节点降级成 normal
    escalation_id = _run_escalation("customer-force-high", "你们这个处理我无法接受", "high")
    record = complaint_api._escalations[escalation_id]

    assert record["status"] == "pending"


def test_completed_graph_does_not_leave_zombie_pending(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages):
            return AIMessage(content="抱歉给您带来不好的体验，我们会尽快核实处理。")

    monkeypatch.setattr(complaint_nodes, "get_llm", lambda *a, **k: FakeLLM())

    escalation_id = _run_escalation("customer-normal", "你好，我想咨询一下", "normal")
    record = complaint_api._escalations[escalation_id]

    # 图已走完（没有 interrupt）→ 必须标记 failed，而不是留在 pending
    assert record["status"] == "failed"


def test_interrupt_payload_carries_original_message():
    escalation_id = _run_escalation("customer-payload", "我要投诉，订单一直不发货", "high")
    record = complaint_api._escalations[escalation_id]

    assert record["message"] == "我要投诉，订单一直不发货"
    assert record["status"] == "pending"

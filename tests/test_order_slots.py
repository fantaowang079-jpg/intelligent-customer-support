# tests/test_order_slots.py
# P0 回归：新订单号必须覆盖旧槽位；查完后清空槽位，避免同一会话永远查第一单。

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage

from backend.agents.order import nodes as order_nodes
from backend.api.v1 import unified_chat


def test_new_order_no_overrides_existing_slot():
    state = {
        "order_no": "88001",
        "phone": "13800138000",
        "messages": [HumanMessage(content="再帮我查一下 88003")],
    }

    updates = asyncio.run(order_nodes.extract_slots_node(state))

    assert updates["order_no"] == "88003"


def test_new_phone_overrides_existing_slot():
    state = {
        "order_no": "88001",
        "phone": "13800138000",
        "messages": [HumanMessage(content="手机号换成 13900139000")],
    }

    updates = asyncio.run(order_nodes.extract_slots_node(state))

    assert updates["phone"] == "13900139000"


def _fake_llm(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages):
            return AIMessage(content="您的订单已发货，预计明日送达。")

    monkeypatch.setattr(order_nodes, "get_llm", lambda *a, **k: FakeLLM())


def test_report_clears_slots_after_success(monkeypatch):
    _fake_llm(monkeypatch)
    state = {
        "order_no": "88001",
        "phone": "13800138000",
        "order_info": {
            "order_no": "88001",
            "product_name": "无线蓝牙耳机",
            "amount": 299.0,
            "status": "shipped",
            "logistics": "已发出",
            "compensation": "",
        },
    }

    out = asyncio.run(order_nodes.report_node(state))

    assert out["order_no"] is None
    assert out["phone"] is None
    assert out["structured_output"]["order_no"] == "88001"


def test_not_found_clears_wrong_order_no_but_keeps_phone():
    state = {
        "order_no": "99999",
        "phone": "13800138000",
        "order_info": None,
        "not_found": True,
    }

    out = asyncio.run(order_nodes.not_found_node(state))

    assert out["order_no"] is None
    assert "phone" not in out  # 保留原手机号


async def _prepare_not_found_state(customer_id: str, session_id: str):
    from backend.agents.order.graph import build_order_graph
    from backend.core.memory import build_thread_id

    graph = build_order_graph()
    config = {"configurable": {"thread_id": build_thread_id(customer_id, session_id)}}
    await graph.ainvoke(
        {
            "messages": [HumanMessage(content="查订单 99999，手机号 13800138000")],
            "customer_id": customer_id,
            "tenant_id": "tenant_default",
            "session_id": session_id,
        },
        config=config,
    )


def test_pending_detection_resumes_after_not_found():
    # 上一轮订单号输错 → 用户下一轮只发正确单号，也必须回到订单图
    asyncio.run(_prepare_not_found_state("customer-retry", "session-retry"))

    pending = asyncio.run(
        unified_chat._find_pending_agent("customer-retry", "session-retry", "88003")
    )

    assert pending == "order"


def _consume_stream(gen):
    async def _inner():
        events = []
        async for ev in gen:
            events.append(json.loads(ev["data"]))
        return events

    return asyncio.run(_inner())


def _meta(events):
    return [e for e in events if e.get("type") == "meta"][-1]


def test_two_order_queries_in_same_session_hit_different_orders(monkeypatch):
    _fake_llm(monkeypatch)

    first = unified_chat.UnifiedChatRequest(
        session_id="session-multi", message="查订单 88001，手机号 13800138000",
    )
    events_1 = _consume_stream(
        unified_chat._stream_order(first, "customer-multi", "tenant_default")
    )
    assert _meta(events_1)["order_no"] == "88001"

    second = unified_chat.UnifiedChatRequest(
        session_id="session-multi", message="再查一下订单 88003，手机号 13800138000",
    )
    events_2 = _consume_stream(
        unified_chat._stream_order(second, "customer-multi", "tenant_default")
    )
    assert _meta(events_2)["order_no"] == "88003"


def test_corrected_order_number_after_not_found(monkeypatch):
    _fake_llm(monkeypatch)

    first = unified_chat.UnifiedChatRequest(
        session_id="session-retry-2", message="查订单 99999，手机号 13800138000",
    )
    events_1 = _consume_stream(
        unified_chat._stream_order(first, "customer-retry-2", "tenant_default")
    )
    assert _meta(events_1)["stage"] == "not_found"

    # 用户下一轮只补充正确单号：挂起检测应把它送回订单图，且沿用上一轮手机号
    assert asyncio.run(
        unified_chat._find_pending_agent("customer-retry-2", "session-retry-2", "88003")
    ) == "order"

    second = unified_chat.UnifiedChatRequest(
        session_id="session-retry-2", message="88003",
    )
    events_2 = _consume_stream(
        unified_chat._stream_order(second, "customer-retry-2", "tenant_default")
    )
    assert _meta(events_2)["order_no"] == "88003"

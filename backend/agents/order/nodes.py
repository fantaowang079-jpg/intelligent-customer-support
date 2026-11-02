# backend/agents/order/nodes.py

import re

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from backend.agents.order.state import OrderState
from backend.agents.order.prompts import SYSTEM_PROMPT, ASK_MISSING_PROMPT, REPORT_PROMPT
from backend.core.llm_factory import get_llm
from backend.core.logger import get_logger
from backend.core.exceptions import OrderNotFoundError
from backend.services.order_service import get_order_service, OrderInfo

logger = get_logger(__name__)


def _get_message_content(msg) -> str:
    if hasattr(msg, "text") and not callable(getattr(msg, "text", None)):
        return msg.text
    if isinstance(msg.content, str):
        return msg.content
    return str(msg.content)


def _last_user_text(state: OrderState) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return _get_message_content(msg)
    return ""


# ── 槽位提取（规则正则）───────────────────────────────────────

_PHONE_RE = re.compile(r"1[3-9]\d{9}")


def _extract_phone(text: str) -> str | None:
    m = _PHONE_RE.search(text)
    return m.group(0) if m else None


def _extract_order_no(text: str) -> str | None:
    # 优先「订单号/单号」后面的数字
    m = re.search(r"(?:订单号|单号|订单编号)\s*[:：]?\s*(\d{5,})", text)
    if m:
        return m.group(1)
    # 先剔除手机号，再取独立的数字串（避免把 11 位手机号截断成订单号）
    text_without_phone = _PHONE_RE.sub(" ", text)
    m = re.search(r"\d{5,10}", text_without_phone)
    return m.group(0) if m else None


async def extract_slots_node(state: OrderState) -> dict:
    """从最新用户消息提取订单号/手机号；本轮显式给出的新值覆盖旧槽位。"""
    text = _last_user_text(state)
    updates: dict = {}

    # 不能用「槽位为空才写入」：否则同一会话第二次查单永远复用第一个订单号
    new_order_no = _extract_order_no(text)
    if new_order_no:
        updates["order_no"] = new_order_no

    new_phone = _extract_phone(text)
    if new_phone:
        updates["phone"] = new_phone

    logger.info(
        "order.extract_slots",
        order_no=updates.get("order_no", state.get("order_no")),
        phone=updates.get("phone", state.get("phone")),
    )
    return updates


def _route_by_slots(state: OrderState) -> str:
    if not state.get("order_no") or not state.get("phone"):
        return "ask_missing"
    return "lookup"


async def ask_missing_node(state: OrderState) -> dict:
    """追问缺失槽位。"""
    missing = []
    if not state.get("order_no"):
        missing.append("订单号")
    if not state.get("phone"):
        missing.append("手机号")

    provided = []
    if state.get("order_no"):
        provided.append(f"订单号：{state['order_no']}")
    if state.get("phone"):
        provided.append(f"手机号：{state['phone']}")

    prompt = ASK_MISSING_PROMPT.format(
        provided="\n".join(provided) or "（暂无）",
        missing="、".join(missing),
    )
    llm = get_llm("order", streaming=True)
    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    reply = _get_message_content(response).strip()

    logger.info("order.ask_missing", missing=missing)
    return {
        "reply": reply,
        "messages": [AIMessage(content=reply)],
        "structured_output": {"stage": "collect", "reply": reply, "missing": missing},
    }


async def lookup_order_node(state: OrderState) -> dict:
    """调用订单系统查询，检测异常单。"""
    service = get_order_service()
    try:
        order: OrderInfo = await service.lookup(state["order_no"], state.get("customer_id"))
        order_info = {
            "order_no": order.order_no,
            "product_name": order.product_name,
            "amount": order.amount,
            "status": order.status,
            "logistics": order.logistics,
            "compensation": order.compensation,
        }
        return {"order_info": order_info, "not_found": False}
    except OrderNotFoundError:
        return {"order_info": None, "not_found": True}
    except Exception as e:
        logger.warning("order.lookup_failed", error=str(e))
        return {"order_info": None, "not_found": True}


def _route_after_lookup(state: OrderState) -> str:
    return "not_found" if state.get("not_found") else "report"


async def not_found_node(state: OrderState) -> dict:
    """订单号查不到。"""
    reply = (
        f"抱歉，没有查到订单号「{state.get('order_no')}」对应的订单。"
        "请确认订单号是否正确，或转人工客服帮您核实。"
    )
    return {
        "reply": reply,
        "messages": [AIMessage(content=reply)],
        "structured_output": {"stage": "not_found", "reply": reply, "order_no": state.get("order_no")},
        # 清掉错误订单号、保留手机号：下一轮用户直接报正确单号即可重查
        "order_no": None,
    }


async def report_node(state: OrderState) -> dict:
    """播报订单状态 + 补偿 + 下一步建议。"""
    order_info = state.get("order_info") or {}
    compensation = order_info.get("compensation") or "（无）"

    order_desc = (
        f"订单号：{order_info.get('order_no')}\n"
        f"商品：{order_info.get('product_name')}\n"
        f"金额：{order_info.get('amount')} 元\n"
        f"状态：{order_info.get('status')}\n"
        f"物流：{order_info.get('logistics')}"
    )

    prompt = REPORT_PROMPT.format(order_info=order_desc, compensation=compensation)
    llm = get_llm("order", streaming=True)
    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    reply = _get_message_content(response).strip()

    logger.info("order.report_done", order_no=order_info.get("order_no"))

    return {
        "reply": reply,
        "messages": [AIMessage(content=reply)],
        "structured_output": {
            "stage": "report",
            "reply": reply,
            "order_no": order_info.get("order_no"),
            "order_info": order_info,
        },
        # 本轮查询已完成：清空槽位，避免下一轮复用同一订单号
        "order_no": None,
        "phone":    None,
    }

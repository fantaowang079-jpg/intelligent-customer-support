# backend/api/v1/unified_chat.py
# 统一客服入口（SSE）：规则前置拦截 → 意图路由 → 分发到各 Agent。

import json
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import HumanMessage, AIMessage

from backend.core.intent_router import (
    route as route_intent,
    _pre_filter,
    _HIGH_RISK_KEYWORDS,
    _STRONG_EMOTION_KEYWORDS,
)
from backend.core.memory import build_thread_id
from backend.dependencies import get_current_user
from backend.core.logger import get_logger
from backend.api.v1.complaint import start_escalation

router = APIRouter()
logger = get_logger(__name__)

_AGENT_DISPLAY = {
    "faq":       "FAQ 问答",
    "order":     "订单查询",
    "return":    "退换货",
    "complaint": "投诉处理",
    "human":     "人工客服",
}

_GUIDANCE = {
    "return": {
        "message": "检测到您需要办理退换货。请提供订单号和退货原因，我们将为您发起退换货申请；"
                   "金额超过 500 元或已使用商品将转人工审批。",
        "action_label": "发起退换货申请",
        "action_url": "/return/apply",
    },
}


class UnifiedChatRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    message:    str = Field(..., min_length=1, max_length=2000, description="用户输入")


def _sse(data: dict) -> dict:
    return {"data": json.dumps(data, ensure_ascii=False)}


def _last_ai_text(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, list):
                return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            return str(content)
    return ""


# 订单槽位判定：挂起图恢复时，判断新消息是否真的在补订单槽位（订单号 / 手机号）
_PHONE_RE = re.compile(r"1[3-9]\d{9}")


def _looks_like_order_slot(message: str) -> bool:
    """新消息是否像在补充订单槽位（订单号 / 手机号）。

    仅当命中数字类槽位、且不含高危/强情绪词时才返回 True，
    避免把 FAQ / 退换货 / 投诉等新诉求误劫持回订单流程。
    """
    text = message.strip()
    if any(k in text for k in _HIGH_RISK_KEYWORDS) or any(k in text for k in _STRONG_EMOTION_KEYWORDS):
        return False
    if _PHONE_RE.search(text):
        return True
    # 剔除手机号后，剩余 5~10 位数字串视为订单号
    return bool(re.search(r"\d{5,10}", _PHONE_RE.sub(" ", text)))


async def _find_pending_agent(customer_id: str, session_id: str, message: str) -> str | None:
    """
    检查该会话在 MemorySaver 中是否存在「未完成 / 等待补充信息」的图。
    有则返回应继续执行的 agent（如 "order"），否则返回 None。

    目的：多轮场景下，用户第二轮可能只补充槽位信息（如手机号），
    单看当前消息 intent_router 会误判成 clarify，导致进不了对应图继续恢复状态。
    仅当新消息确实携带订单槽位时才恢复，避免劫持新话题。
    """
    if not _looks_like_order_slot(message):
        return None

    from backend.agents.order.graph import build_order_graph

    thread_id = build_thread_id(customer_id, session_id)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph = build_order_graph()
        snapshot = await graph.aget_state(config)
    except Exception as e:
        logger.warning("unified_chat.pending_check_failed", error=str(e))
        return None

    if not snapshot or not snapshot.values:
        return None

    sv = snapshot.values
    # 订单状态机「未完成」判据：订单尚未查到（order_info 为空）且非「订单不存在」→ 仍在收集槽位
    # 其它多轮 Agent 若需要挂起续跑，可在此追加同类判据。
    if sv.get("order_info") is None and not sv.get("not_found", False):
        return "order"

    return None


@router.post("/stream")
async def unified_chat_stream(
    req: UnifiedChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    统一客服流式接口（SSE）。

    事件类型：
      routing_decision  路由决策（intent/urgency/route_to/reason）
      token             流式回答片段
      meta              元数据（answer_mode/stage/risk_level 等）
      guidance          引导卡片（退换货等）
      done              流结束
      error             异常
    """
    customer_id = current_user["user_id"]
    tenant_id = current_user["tenant_id"]

    async def event_generator():
        # ── Step 0：规则前置拦截（零 Token）──────────────────
        pre_reply = _pre_filter(req.message)
        if pre_reply is not None:
            yield _sse({"type": "token", "content": pre_reply})
            yield _sse({"type": "done"})
            return

        # ── Step 0.5：挂起图恢复 ─────────────────────────────
        # 若该会话存在「未完成」的图（如订单状态机正在收集槽位），
        # 直接跳过意图路由，把请求交给对应图继续执行。
        pending_agent = await _find_pending_agent(customer_id, req.session_id, req.message)
        if pending_agent is not None:
            logger.info(
                "unified_chat.resume_pending_agent",
                agent=pending_agent,
                session_id=req.session_id,
            )
            yield _sse({
                "type": "routing_decision",
                "intent": pending_agent,
                "urgency": "normal",
                "route_to": pending_agent,
                "agent_display": _AGENT_DISPLAY.get(pending_agent, ""),
                "reason": "继续上一轮未完成的会话",
                "resume": True,
            })
            if pending_agent == "order":
                async for ev in _stream_order(req, customer_id, tenant_id):
                    yield ev
            yield _sse({"type": "done"})
            return

        # ── Step 1：意图路由 ────────────────────────────────
        decision = await route_intent(req.message)

        yield _sse({
            "type": "routing_decision",
            "intent": decision.intent,
            "urgency": decision.urgency,
            "route_to": decision.route_to,
            "agent_display": _AGENT_DISPLAY.get(decision.route_to, ""),
            "reason": decision.reason,
        })

        # ── Step 2：按路由分发 ──────────────────────────────
        if decision.route_to == "faq":
            async for ev in _stream_faq(req, customer_id, tenant_id):
                yield ev

        elif decision.route_to == "order":
            async for ev in _stream_order(req, customer_id, tenant_id):
                yield ev

        elif decision.route_to == "return":
            g = _GUIDANCE["return"]
            yield _sse({
                "type": "guidance",
                "message": g["message"],
                "action_label": g["action_label"],
                "action_url": g["action_url"],
            })

        elif decision.route_to == "complaint":
            if decision.urgency == "high":
                # 高危投诉：后台启动 interrupt 转人工，立即告知客户
                escalation_id = start_escalation(
                    customer_id, tenant_id, req.message, "high", decision.reason,
                )
                yield _sse({
                    "type": "token",
                    "content": "已为您转接人工坐席，专属客服将尽快与您联系，请保持在线。感谢您的耐心。",
                })
                yield _sse({
                    "type": "meta", "risk_level": "high", "escalated": True,
                    "escalation_id": escalation_id,
                })
            else:
                async for ev in _stream_complaint(req, customer_id, tenant_id):
                    yield ev

        elif decision.route_to == "human":
            yield _sse({
                "type": "token",
                "content": "好的，正在为您转接人工客服，请稍候（预计等待 1-2 分钟）…",
            })

        else:  # clarify
            yield _sse({
                "type": "guidance",
                "message": "我还不太确定您的具体需求，能否描述得更具体一些？"
                           "例如：查询订单、申请退换货、咨询产品政策，或直接说「转人工」。",
                "action_label": "",
                "action_url": "",
            })

        yield _sse({"type": "done"})

    return EventSourceResponse(event_generator())


async def _stream_faq(req: UnifiedChatRequest, customer_id: str, tenant_id: str):
    """流式执行 FAQ RAG Agent。"""
    from backend.agents.faq.graph import build_faq_graph

    graph = build_faq_graph()
    thread_id = build_thread_id(customer_id, req.session_id)
    initial_state = {
        "messages":    [HumanMessage(content=req.message)],
        "customer_id": customer_id,
        "tenant_id":   tenant_id,
        "session_id":  req.session_id,
    }
    config = {"configurable": {"thread_id": thread_id}}

    streamed = False
    answer_mode = None
    sources: list[str] = []
    confidence = 0.0

    try:
        async for event in graph.astream_events(initial_state, config=config, version="v2"):
            evt = event["event"]
            node = event.get("metadata", {}).get("langgraph_node", "")
            if evt == "on_chat_model_stream" and node == "generate_rag":
                chunk = event["data"].get("chunk")
                if chunk and chunk.content:
                    streamed = True
                    yield _sse({"type": "token", "content": chunk.content})
            elif evt == "on_chain_end" and node == "generate_rag":
                out = event["data"].get("output", {})
                if out.get("answer_mode"):
                    answer_mode = out["answer_mode"]
                if out.get("sources"):
                    sources = out["sources"]
                confidence = (out.get("structured_output") or {}).get("confidence", confidence)
    except Exception as e:
        logger.error("unified_chat.faq_error", error=str(e), exc_info=True)
        yield _sse({"type": "error", "message": "问答服务异常，请稍后重试"})
        return

    final = await graph.aget_state(config)
    sv = final.values if final else {}

    if not streamed:
        reply = sv.get("answer") or _last_ai_text(sv.get("messages", []))
        if reply:
            yield _sse({"type": "token", "content": reply})
    if answer_mode is None:
        answer_mode = sv.get("answer_mode")
    if not sources:
        sources = sv.get("sources", [])
    yield _sse({
        "type": "meta",
        "answer_mode": answer_mode,
        "confidence": confidence,
        "sources": sources,
    })


async def _stream_order(req: UnifiedChatRequest, customer_id: str, tenant_id: str):
    """流式执行订单状态机 Agent（多轮）。"""
    from backend.agents.order.graph import build_order_graph

    graph = build_order_graph()
    thread_id = build_thread_id(customer_id, req.session_id)
    initial_state = {
        "messages":    [HumanMessage(content=req.message)],
        "customer_id": customer_id,
        "tenant_id":   tenant_id,
        "session_id":  req.session_id,
    }
    config = {"configurable": {"thread_id": thread_id}}

    streamed = False
    token_nodes = {"ask_missing", "report"}

    try:
        async for event in graph.astream_events(initial_state, config=config, version="v2"):
            evt = event["event"]
            node = event.get("metadata", {}).get("langgraph_node", "")
            if evt == "on_chat_model_stream" and node in token_nodes:
                chunk = event["data"].get("chunk")
                if chunk and chunk.content:
                    streamed = True
                    yield _sse({"type": "token", "content": chunk.content})
    except Exception as e:
        logger.error("unified_chat.order_error", error=str(e), exc_info=True)
        yield _sse({"type": "error", "message": "订单查询服务异常，请稍后重试"})
        return

    final = await graph.aget_state(config)
    sv = final.values if final else {}

    if not streamed:
        reply = sv.get("reply") or _last_ai_text(sv.get("messages", []))
        if reply:
            yield _sse({"type": "token", "content": reply})

    structured = sv.get("structured_output") or {}
    yield _sse({
        "type": "meta",
        "stage": structured.get("stage", ""),
        "order_no": structured.get("order_no") or sv.get("order_no"),
    })


async def _stream_complaint(req: UnifiedChatRequest, customer_id: str, tenant_id: str):
    """流式执行投诉 Agent（低风险安抚）。"""
    from backend.agents.complaint.graph import build_complaint_graph

    graph = build_complaint_graph()
    thread_id = build_thread_id(customer_id, req.session_id)
    initial_state = {
        "messages":    [HumanMessage(content=req.message)],
        "customer_id": customer_id,
        "tenant_id":   tenant_id,
        "session_id":  req.session_id,
    }
    config = {"configurable": {"thread_id": thread_id}}

    streamed = False
    try:
        async for event in graph.astream_events(initial_state, config=config, version="v2"):
            evt = event["event"]
            node = event.get("metadata", {}).get("langgraph_node", "")
            if evt == "on_chat_model_stream" and node == "comfort":
                chunk = event["data"].get("chunk")
                if chunk and chunk.content:
                    streamed = True
                    yield _sse({"type": "token", "content": chunk.content})
    except Exception as e:
        logger.error("unified_chat.complaint_error", error=str(e), exc_info=True)
        yield _sse({"type": "error", "message": "投诉服务异常，请稍后重试"})
        return

    final = await graph.aget_state(config)
    sv = final.values if final else {}

    if not streamed:
        reply = sv.get("reply") or _last_ai_text(sv.get("messages", []))
        if reply:
            yield _sse({"type": "token", "content": reply})

    yield _sse({
        "type": "meta",
        "risk_level": sv.get("risk_level", "normal"),
        "escalated": False,
    })

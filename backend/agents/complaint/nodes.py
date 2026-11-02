# backend/agents/complaint/nodes.py

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.types import interrupt

from backend.agents.complaint.state import ComplaintState
from backend.agents.complaint.prompts import SYSTEM_PROMPT, COMFORT_PROMPT
from backend.core.llm_factory import get_llm
from backend.core.logger import get_logger
from backend.core.intent_router import _HIGH_RISK_KEYWORDS, _STRONG_EMOTION_KEYWORDS

logger = get_logger(__name__)


def _get_message_content(msg) -> str:
    if hasattr(msg, "text") and not callable(getattr(msg, "text", None)):
        return msg.text
    if isinstance(msg.content, str):
        return msg.content
    return str(msg.content)


def _last_user_text(state: ComplaintState) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return _get_message_content(msg)
    return ""


async def emotion_detect_node(state: ComplaintState) -> dict:
    """
    情绪识别 + 诉求归类：规则优先（安全红线，宁可多转人工，不可漏判）。
    命中高危/强情绪词 → risk_level=high。
    """
    text = _last_user_text(state)

    high_hit = [k for k in _HIGH_RISK_KEYWORDS if k in text]
    emotion_hit = [k for k in _STRONG_EMOTION_KEYWORDS if k in text]

    if high_hit or emotion_hit:
        risk_level = "high"
        reason = f"命中高危/强情绪词：{(high_hit + emotion_hit)[:3]}"
    elif state.get("force_high"):
        # 路由侧（LLM/规则）已判定高危：只升不降，避免安全红线被文本规则降级
        risk_level = "high"
        reason = state.get("reason") or "路由判定为高危，保留人工介入"
    else:
        risk_level = "normal"
        reason = "情绪强度正常，先安抚处理"

    logger.info("complaint.emotion_detect", risk_level=risk_level, reason=reason)
    return {"risk_level": risk_level, "reason": reason}


def _route_by_risk(state: ComplaintState) -> str:
    return "escalate" if state.get("risk_level") == "high" else "comfort"


async def comfort_node(state: ComplaintState) -> dict:
    """低风险：生成安抚话术（流式）。"""
    text = _last_user_text(state)
    prompt = COMFORT_PROMPT.format(message=text[:500])
    llm = get_llm("complaint", streaming=True)
    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    reply = _get_message_content(response).strip()

    logger.info("complaint.comfort_done", reply_length=len(reply))

    return {
        "reply": reply,
        "escalated": False,
        "messages": [AIMessage(content=reply)],
        "structured_output": {"risk_level": "normal", "reply": reply, "escalated": False},
    }


async def escalate_node(state: ComplaintState) -> dict:
    """
    高危投诉：interrupt 暂停，把上下文摘要交接给人工坐席。
    人工坐席接管后（Command resume）落审计日志，向客户返回确认话术。
    """
    from backend.services.after_sale_service import get_after_sale_service

    # 上下文摘要（交接给人工坐席）
    handoff = {
        "session_id":  state["session_id"],
        "customer_id": state.get("customer_id", ""),
        "message":     _last_user_text(state),
        "risk_level":  state.get("risk_level", "high"),
        "reason":      state.get("reason", ""),
        "summary":     "高危投诉，需人工坐席优先处理，请尽快接管。",
    }

    decision = interrupt(handoff)

    # resume 后：落审计「已接管」
    try:
        await get_after_sale_service().log({
            "type": "complaint_taken_over",
            "session_id": state["session_id"],
            "agent_id": decision.get("agent_id", ""),
        })
    except Exception as e:
        logger.warning("complaint.log_failed", error=str(e))

    reply = "已为您转接人工坐席，专属客服将尽快与您联系，请保持在线。感谢您的耐心。"

    logger.info("complaint.escalated", session_id=state["session_id"])

    return {
        "reply": reply,
        "escalated": True,
        "escalation": handoff,
        "messages": [AIMessage(content=reply)],
        "structured_output": {
            "risk_level": "high",
            "reply": reply,
            "escalated": True,
            "handoff": handoff,
        },
    }

# backend/api/v1/complaint.py
# 投诉升级 HitL 接口：高危投诉 interrupt 转人工，人工坐席接管后 resume。

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from langgraph.types import Command

from backend.agents.complaint.graph import build_complaint_graph
from backend.dependencies import get_current_user
from backend.core.memory import build_thread_id
from backend.core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

_graph = build_complaint_graph()

_background_tasks: set[asyncio.Task] = set()

# 内存转人工登记表：escalation_id → {customer_id, session_id, message, risk_level, reason, thread_id, status}
_escalations: dict[str, dict] = {}


def start_escalation(
    customer_id: str,
    tenant_id: str,
    message: str,
    risk_level: str,
    reason: str,
) -> str:
    """
    启动高危投诉转人工流程（后台跑图，interrupt 在 escalate 节点暂停）。
    供 unified_chat 在高危投诉时调用。
    """
    escalation_id = str(uuid.uuid4())
    thread_id = build_thread_id(customer_id, escalation_id)

    _escalations[escalation_id] = {
        "escalation_id": escalation_id,
        "customer_id": customer_id,
        "message": message,
        "risk_level": risk_level,
        "reason": reason,
        "thread_id": thread_id,
        "status": "pending",
    }

    initial_state = {
        "messages":      [],
        "customer_id":   customer_id,
        "tenant_id":     tenant_id,
        "session_id":    escalation_id,
        "risk_level":    risk_level,
        "reason":        reason,
        "reply":         "",
        "escalated":     False,
        "escalation":    None,
        "structured_output": None,
        "fallback_used": False,
    }
    config = {"configurable": {"thread_id": thread_id}}

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if t.cancelled():
            return
        if t.exception() is not None:
            logger.error("complaint.background_failed", escalation_id=escalation_id, error=str(t.exception()))
            _escalations[escalation_id]["status"] = "failed"

    task = asyncio.create_task(_graph.ainvoke(initial_state, config=config))
    _background_tasks.add(task)
    task.add_done_callback(_on_done)

    logger.info("complaint.escalation_started", escalation_id=escalation_id)
    return escalation_id


class ResolveRequest(BaseModel):
    note: str = Field(default="", description="接管备注")


@router.get("/pending")
async def list_pending(current_user: dict = Depends(get_current_user)):
    """人工坐席获取待接管的投诉。"""
    items = [
        {
            "escalation_id": e["escalation_id"],
            "customer_id":   e["customer_id"],
            "message":       e["message"],
            "risk_level":    e["risk_level"],
            "reason":        e["reason"],
            "status":        e["status"],
        }
        for e in _escalations.values()
        if e["status"] == "pending"
    ]
    return {"items": items, "total": len(items)}


@router.post("/{escalation_id}/resolve")
async def resolve_escalation(
    escalation_id: str,
    req: ResolveRequest,
    current_user: dict = Depends(get_current_user),
):
    """人工坐席接管：Command(resume) 恢复图，落审计日志。"""
    esc = _escalations.get(escalation_id)
    if not esc:
        raise HTTPException(status_code=404, detail="投诉升级记录不存在")

    config = {"configurable": {"thread_id": esc["thread_id"]}}
    decision = {"action": "taken_over", "agent_id": current_user["user_id"], "note": req.note}

    try:
        result = await _graph.ainvoke(Command(resume=decision), config=config)
    except Exception as e:
        logger.error("complaint.resolve_failed", escalation_id=escalation_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"接管失败：{e}")

    esc["status"] = "resolved"

    logger.info("complaint.resolved", escalation_id=escalation_id, agent_id=current_user["user_id"])

    return {
        "escalation_id": escalation_id,
        "status": "resolved",
        "message": "已由人工坐席接管处理",
    }

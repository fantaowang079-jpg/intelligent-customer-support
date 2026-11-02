# backend/api/v1/complaint.py
# 投诉升级 HitL 接口：高危投诉 interrupt 转人工，人工坐席接管后 resume。

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from langgraph.types import Command
from langchain_core.messages import HumanMessage

from backend.agents.complaint.graph import build_complaint_graph
from backend.dependencies import require_agent
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
        "status": "processing",
    }

    initial_state = {
        # 必须带上原始消息：情绪节点要靠它判定高危，空消息会被重判为 normal
        "messages":      [HumanMessage(content=message)],
        "customer_id":   customer_id,
        "tenant_id":     tenant_id,
        "session_id":    escalation_id,
        "risk_level":    risk_level,
        "force_high":    risk_level == "high",
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
            return

        # 只有真正停在 interrupt 才算「待接管」；否则说明图已走完，
        # 不能让记录停留在 pending（工作台点接管会 resume 一个已结束的图）。
        result_state = t.result() or {}
        if result_state.get("__interrupt__"):
            _escalations[escalation_id]["status"] = "pending"
            logger.info("complaint.waiting_for_agent", escalation_id=escalation_id)
        else:
            logger.warning("complaint.no_interrupt", escalation_id=escalation_id)
            _escalations[escalation_id]["status"] = "failed"

    task = asyncio.create_task(_graph.ainvoke(initial_state, config=config))
    _background_tasks.add(task)
    task.add_done_callback(_on_done)

    logger.info("complaint.escalation_started", escalation_id=escalation_id)
    return escalation_id


class ResolveRequest(BaseModel):
    note: str = Field(default="", description="接管备注")


@router.get("/pending")
async def list_pending(current_user: dict = Depends(require_agent)):
    """人工坐席获取待接管的投诉（仅坐席可见）。"""
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
    current_user: dict = Depends(require_agent),
):
    """人工坐席接管：Command(resume) 恢复图，落审计日志（仅坐席可调用）。"""
    esc = _escalations.get(escalation_id)
    if not esc:
        raise HTTPException(status_code=404, detail="投诉升级记录不存在")

    # 幂等/状态机校验：只有真正停在 interrupt 的记录才能接管，避免重复 resume
    if esc["status"] == "processing":
        raise HTTPException(status_code=409, detail="投诉正在识别中，请稍后刷新")
    if esc["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"该投诉当前状态为 {esc['status']}，不可重复接管")

    config = {"configurable": {"thread_id": esc["thread_id"]}}
    decision = {"action": "taken_over", "agent_id": current_user["user_id"], "note": req.note}

    esc["status"] = "resolving"  # 并发请求先拦住
    try:
        await _graph.ainvoke(Command(resume=decision), config=config)
    except Exception as e:
        esc["status"] = "pending"  # 恢复失败可重试
        logger.error("complaint.resolve_failed", escalation_id=escalation_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"接管失败：{e}")

    esc["status"] = "resolved"
    esc["agent_id"] = current_user["user_id"]

    logger.info("complaint.resolved", escalation_id=escalation_id, agent_id=current_user["user_id"])

    return {
        "escalation_id": escalation_id,
        "status": "resolved",
        "message": "已由人工坐席接管处理",
    }

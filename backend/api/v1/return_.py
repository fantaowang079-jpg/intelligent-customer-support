# backend/api/v1/return_.py
# 退换货流程 HitL 接口：apply / pending / review / confirm（对应 EduAgent 的 exam.py）。

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from langgraph.types import Command

from backend.agents.return_.graph import build_return_graph
from backend.dependencies import get_current_user
from backend.core.memory import build_thread_id
from backend.core.logger import get_logger
from backend.services.order_service import get_order_service
from backend.core.exceptions import OrderNotFoundError

router = APIRouter()
logger = get_logger(__name__)

_graph = build_return_graph()

# 持有 background task 引用，防止 asyncio GC 回收未完成任务
_background_tasks: set[asyncio.Task] = set()

# 内存应用注册表（无 DB）：application_id → 应用元信息
_applications: dict[str, dict] = {}


class ApplyRequest(BaseModel):
    order_no: str = Field(..., description="订单号")
    reason:   str = Field(..., description="退货原因")


class ConfirmRequest(BaseModel):
    action: str = Field(..., description="approve / reject")
    note:   str = Field(default="", description="审批备注")


@router.post("/apply", status_code=202)
async def apply_return(
    req: ApplyRequest,
    current_user: dict = Depends(get_current_user),
):
    """发起退换货申请：后台异步跑图，自动通过直接完成，需人工则 interrupt 暂停。"""
    # 校验订单存在
    try:
        await get_order_service().lookup(req.order_no, current_user["user_id"])
    except OrderNotFoundError:
        raise HTTPException(status_code=400, detail="订单号不存在，请确认后重试")

    application_id = str(uuid.uuid4())
    customer_id = current_user["user_id"]
    thread_id = build_thread_id(customer_id, application_id)

    _applications[application_id] = {
        "application_id": application_id,
        "order_no": req.order_no,
        "customer_id": customer_id,
        "reason": req.reason,
        "status": "ai_processing",
        "thread_id": thread_id,
    }

    initial_state = {
        "messages":        [],
        "customer_id":     customer_id,
        "tenant_id":       current_user["tenant_id"],
        "session_id":      application_id,
        "application_id":  application_id,
        "order_no":        req.order_no,
        "reason":          req.reason,
        "product_name":    "",
        "amount":          0.0,
        "is_used":         False,
        "is_in_warranty":  True,
        "rule_audit":      None,
        "decision":        None,
        "final_result":    None,
        "structured_output": None,
        "fallback_used":   False,
    }
    config = {"configurable": {"thread_id": thread_id}}

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("return.background_failed", application_id=application_id, error=str(exc))
            _applications[application_id]["status"] = "failed"
            return
        result_state = t.result()
        if result_state.get("final_result"):
            _applications[application_id]["status"] = result_state["final_result"].get("status", "done")
        elif result_state.get("rule_audit", {}).get("needs_review"):
            _applications[application_id]["status"] = "pending_review"
        else:
            _applications[application_id]["status"] = "done"

    task = asyncio.create_task(_graph.ainvoke(initial_state, config=config))
    _background_tasks.add(task)
    task.add_done_callback(_on_done)

    logger.info("return.applied", application_id=application_id, order_no=req.order_no)

    return {
        "application_id": application_id,
        "status": "ai_processing",
        "message": "退换货申请已提交，正在审核中…",
    }


@router.get("/pending")
async def list_pending(current_user: dict = Depends(get_current_user)):
    """人工坐席获取所有待审批的申请（status=pending_review）。"""
    items = [
        {
            "application_id": a["application_id"],
            "order_no":       a["order_no"],
            "customer_id":    a["customer_id"],
            "reason":         a["reason"],
            "status":         a["status"],
        }
        for a in _applications.values()
        if a["status"] == "pending_review"
    ]
    return {"items": items, "total": len(items)}


@router.get("/{application_id}/review")
async def get_review(application_id: str, current_user: dict = Depends(get_current_user)):
    """人工坐席读取 AI 规则审核详情（从 MemorySaver 读图暂停时的 State）。"""
    app = _applications.get(application_id)
    if not app:
        raise HTTPException(status_code=404, detail="申请不存在")

    config = {"configurable": {"thread_id": app["thread_id"]}}
    snapshot = await _graph.aget_state(config)
    if not snapshot or not snapshot.values:
        raise HTTPException(status_code=404, detail="审核尚未完成或记录不存在")

    sv = snapshot.values
    return {
        "application_id": application_id,
        "order_no":       sv.get("order_no", ""),
        "product_name":   sv.get("product_name", ""),
        "amount":         sv.get("amount", 0.0),
        "reason":         sv.get("reason", ""),
        "rule_audit":     sv.get("rule_audit", {}),
    }


@router.post("/{application_id}/confirm")
async def confirm_review(
    application_id: str,
    req: ConfirmRequest,
    current_user: dict = Depends(get_current_user),
):
    """人工审批：恢复 interrupt，触发 apply_decision → execute。"""
    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 只能为 approve 或 reject")

    app = _applications.get(application_id)
    if not app:
        raise HTTPException(status_code=404, detail="申请不存在")

    config = {"configurable": {"thread_id": app["thread_id"]}}
    decision = {"action": req.action, "agent_id": current_user["user_id"], "note": req.note}

    try:
        result = await _graph.ainvoke(Command(resume=decision), config=config)
    except Exception as e:
        logger.error("return.confirm_failed", application_id=application_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"审批执行失败：{e}")

    structured = result.get("structured_output", {}) or {}
    app["status"] = structured.get("status", "done")

    logger.info("return.confirmed", application_id=application_id, status=app["status"])

    return {
        "application_id": application_id,
        "status": structured.get("status"),
        "message": structured.get("message", ""),
    }

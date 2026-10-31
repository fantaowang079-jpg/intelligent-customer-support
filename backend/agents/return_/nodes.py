# backend/agents/return_/nodes.py

from langgraph.types import interrupt

from backend.agents.return_.state import ReturnState
from backend.core.logger import get_logger
from backend.core.exceptions import OrderNotFoundError
from backend.services.order_service import get_order_service
from backend.services.after_sale_service import (
    get_after_sale_service,
    ReturnApplication,
)

logger = get_logger(__name__)


def _build_application(state: ReturnState) -> ReturnApplication:
    """从 State 构造退换货申请对象。"""
    return ReturnApplication(
        application_id=state["application_id"],
        customer_id=state.get("customer_id", ""),
        order_no=state["order_no"],
        reason=state.get("reason", ""),
        amount=state.get("amount", 0.0),
        is_used=state.get("is_used", False),
        is_in_warranty=state.get("is_in_warranty", True),
        product_name=state.get("product_name", ""),
    )


async def rule_audit_node(state: ReturnState) -> dict:
    """
    规则自动审核：先查订单系统补全订单信息，再按规则审核。
    在保期 / 未拆封 / 金额 ≤ 阈值 → 自动通过；超阈值或已使用类目 → 强制人工。
    """
    service = get_after_sale_service()
    order_service = get_order_service()

    # ── 查订单补全信息 ─────────────────────────────────────────
    product_name, amount, is_used, is_in_warranty = "", 0.0, False, True
    try:
        order = await order_service.lookup(state["order_no"], state.get("customer_id"))
        product_name = order.product_name
        amount = order.amount
        is_used = order.is_used
        is_in_warranty = order.is_in_warranty
    except OrderNotFoundError:
        logger.warning("return.order_not_found", order_no=state["order_no"])
        return {
            "product_name": "", "amount": 0.0, "is_used": False, "is_in_warranty": False,
            "rule_audit": {"auto_approved": False, "needs_review": False, "reason": "订单不存在，自动拒绝"},
        }
    except Exception as e:
        logger.warning("return.order_lookup_failed", error=str(e))
        return {
            "product_name": "", "amount": 0.0, "is_used": False, "is_in_warranty": False,
            "rule_audit": {"auto_approved": False, "needs_review": True, "reason": f"订单查询失败：{e}"},
        }

    # ── 规则审核 ───────────────────────────────────────────────
    enriched = {**state, "product_name": product_name, "amount": amount,
                "is_used": is_used, "is_in_warranty": is_in_warranty}
    audit = await service.audit(_build_application(enriched))

    # 自动通过也要写审计日志（关键业务规则）
    if audit.auto_approved:
        await service.log({
            "type": "return_auto_approved",
            "application_id": state["application_id"],
            "order_no": state["order_no"],
            "reason": audit.reason,
        })

    logger.info(
        "return.rule_audit_done",
        application_id=state["application_id"],
        auto_approved=audit.auto_approved,
        needs_review=audit.needs_review,
    )

    return {
        "product_name": product_name,
        "amount": amount,
        "is_used": is_used,
        "is_in_warranty": is_in_warranty,
        "rule_audit": {
            "auto_approved": audit.auto_approved,
            "needs_review": audit.needs_review,
            "reason": audit.reason,
        },
    }


def _route_by_audit(state: ReturnState) -> str:
    """审核后路由：需人工 → interrupt 节点；否则 → 直接决策执行。"""
    return "manual" if state.get("rule_audit", {}).get("needs_review") else "auto"


async def manual_review_node(state: ReturnState) -> dict:
    """
    Human-in-the-Loop 暂停点。interrupt() 冻结图执行，State 保存到 MemorySaver。
    人工通过 POST /confirm 传入 Command(resume=decision) 恢复。
    """
    display_data = {
        "application_id": state["application_id"],
        "order_no":       state["order_no"],
        "product_name":   state.get("product_name", ""),
        "amount":         state.get("amount", 0.0),
        "reason":         state.get("reason", ""),
        "rule_audit":     state.get("rule_audit", {}),
        "message":        "该退换货申请需人工审批，请确认是否通过。",
    }

    decision = interrupt(display_data)

    logger.info(
        "return.manual_review_resumed",
        application_id=state["application_id"],
        action=decision.get("action", "unknown"),
    )

    return {"decision": decision}


async def apply_decision_node(state: ReturnState) -> dict:
    """合并决策：人工决策优先；自动路径按规则审核结果默认 approve / reject。"""
    decision = state.get("decision")
    if decision is None:
        audit = state.get("rule_audit", {})
        if audit.get("auto_approved"):
            decision = {"action": "approve", "agent_id": "auto"}
        else:
            decision = {"action": "reject", "agent_id": "auto", "note": audit.get("reason", "")}

    return {"decision": decision}


async def execute_node(state: ReturnState) -> dict:
    """执行退款/换货（自动通过与人工通过都走到这里），并落审计日志。"""
    service = get_after_sale_service()
    application = _build_application(state)
    decision = state.get("decision") or {"action": "reject", "agent_id": "auto"}

    result = await service.execute(application, decision)

    return {
        "final_result": result,
        "structured_output": {
            "application_id": application.application_id,
            "order_no": application.order_no,
            "status": result["status"],
            "message": result["message"],
            "decision": decision,
        },
    }

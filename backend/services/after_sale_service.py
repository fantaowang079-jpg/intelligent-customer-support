# backend/services/after_sale_service.py
# 退换货审批 + 审计日志的可插拔接口 + 内存 Mock 实现。

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from backend.core.logger import get_logger

logger = get_logger(__name__)

# 规则阈值：金额 > 500 或属「已使用」类目 → 强制人工审批（HitL 不可绕过）
AMOUNT_THRESHOLD = 500.0


@dataclass
class ReturnApplication:
    """退换货申请。"""
    application_id: str
    customer_id:    str
    order_no:       str
    reason:         str
    amount:         float
    is_used:        bool
    is_in_warranty: bool
    product_name:   str = ""


@dataclass
class AuditResult:
    """规则审核结果。"""
    auto_approved:  bool
    needs_review:   bool
    reason:         str
    decision:       Optional[dict] = None


class AfterSaleService:
    """退换货审批抽象接口。"""

    async def audit(self, application: ReturnApplication) -> AuditResult:
        raise NotImplementedError

    async def execute(self, application: ReturnApplication, decision: dict) -> dict:
        raise NotImplementedError

    async def log(self, entry: dict) -> None:
        raise NotImplementedError


class MockAfterSaleService(AfterSaleService):
    """内存 Mock：规则审核 + 内存审计日志。"""

    def __init__(self):
        self._audit_log: list[dict] = []

    async def audit(self, application: ReturnApplication) -> AuditResult:
        """规则自动审核：
        - 不在保期 → 直接拒绝
        - 金额 > 阈值 或 已使用类目 → 强制人工审批（interrupt）
        - 其余 → 自动通过
        """
        if not application.is_in_warranty:
            return AuditResult(
                auto_approved=False, needs_review=False,
                reason="超出保修期，自动拒绝",
            )
        if application.is_used:
            return AuditResult(
                auto_approved=False, needs_review=True,
                reason="属「已使用」类目，需人工审批",
            )
        if application.amount > AMOUNT_THRESHOLD:
            return AuditResult(
                auto_approved=False, needs_review=True,
                reason=f"金额 {application.amount} 元超过 {AMOUNT_THRESHOLD} 元阈值，需人工审批",
            )
        return AuditResult(
            auto_approved=True, needs_review=False,
            reason="在保期且未超阈值，自动通过",
        )

    async def execute(self, application: ReturnApplication, decision: dict) -> dict:
        """执行退款 / 换货，并落审计日志。"""
        action = decision.get("action", "approve")
        if action == "approve":
            result = {
                "status": "refunded",
                "application_id": application.application_id,
                "order_no": application.order_no,
                "refund_amount": application.amount,
                "message": f"已发起退款 {application.amount} 元，预计 3-5 个工作日原路退回。",
            }
        else:
            result = {
                "status": "rejected",
                "application_id": application.application_id,
                "order_no": application.order_no,
                "message": "申请未通过人工审批，已驳回。",
            }
        await self.log({
            "application_id": application.application_id,
            "order_no": application.order_no,
            "action": action,
            "result": result["status"],
            "reviewed_by": decision.get("agent_id", ""),
            "note": decision.get("note", ""),
        })
        logger.info("after_sale.execute", application_id=application.application_id, result=result["status"])
        return result

    async def log(self, entry: dict) -> None:
        """审计日志：任何自动通过 / 人工决策 / 转人工都要记录。"""
        entry = {"id": str(uuid.uuid4()), "ts": time.time(), **entry}
        self._audit_log.append(entry)
        logger.info("after_sale.audit_logged", **{k: v for k, v in entry.items() if k != "id"})

    def list_logs(self) -> list[dict]:
        return list(self._audit_log)


_after_sale_service: Optional[AfterSaleService] = None


def get_after_sale_service() -> MockAfterSaleService:
    """获取售后审批服务单例（默认 Mock）。"""
    global _after_sale_service
    if _after_sale_service is None:
        _after_sale_service = MockAfterSaleService()
    return _after_sale_service

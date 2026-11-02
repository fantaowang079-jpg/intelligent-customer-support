# backend/services/order_service.py
# 订单系统可插拔接口 + 内存 Mock 实现。
# 业务代码只依赖 OrderService 抽象，后续可无缝替换为真实订单系统 API / DB。

from dataclasses import dataclass, field
from typing import Optional

from backend.core.exceptions import OrderNotFoundError
from backend.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OrderInfo:
    """订单信息（订单系统返回的统一结构）。"""
    order_no:      str
    customer_id:   str
    product_name:  str
    amount:        float            # 订单金额（元）
    status:        str              # pending_ship / shipped / delivered / completed / cancelled
    logistics:     str              # 物流状态描述
    is_used:       bool = False     # 是否属于「已使用」类目（影响退换货规则）
    is_in_warranty: bool = True     # 是否在保期内
    compensation:  str = ""         # 异常单补偿话术（空 = 正常单）
    raw:           dict = field(default_factory=dict)


class OrderService:
    """订单系统抽象接口。"""

    async def lookup(self, order_no: str, customer_id: Optional[str] = None) -> OrderInfo:
        raise NotImplementedError


# ── 内存 Mock 实现 ────────────────────────────────────────────

_MOCK_ORDERS: dict[str, dict] = {
    "88001": {
        "order_no": "88001", "customer_id": "demo-customer",
        "product_name": "无线蓝牙耳机", "amount": 299.0,
        "status": "shipped", "logistics": "已从上海仓发出，预计明日送达",
        "is_used": False, "is_in_warranty": True, "compensation": "",
    },
    "88002": {
        "order_no": "88002", "customer_id": "demo-customer",
        "product_name": "智能手环", "amount": 399.0,
        "status": "pending_ship", "logistics": "商家尚未发货",
        "is_used": False, "is_in_warranty": True,
        "compensation": "订单已超时未发货，可为您申请 5 元补偿券并优先催单。",
    },
    "88003": {
        "order_no": "88003", "customer_id": "demo-customer",
        "product_name": "机械键盘", "amount": 899.0,
        "status": "delivered", "logistics": "已签收",
        "is_used": False, "is_in_warranty": True, "compensation": "",
    },
    "88004": {
        "order_no": "88004", "customer_id": "demo-customer",
        "product_name": "护肤套装", "amount": 520.0,
        "status": "delivered", "logistics": "已签收",
        "is_used": True, "is_in_warranty": True, "compensation": "",
    },
    "88005": {
        "order_no": "88005", "customer_id": "demo-customer",
        "product_name": "旧款显示器", "amount": 199.0,
        "status": "delivered", "logistics": "已签收",
        "is_used": False, "is_in_warranty": False, "compensation": "",
    },
}


class MockOrderService(OrderService):
    """内存 Mock 订单服务。"""

    async def lookup(self, order_no: str, customer_id: Optional[str] = None) -> OrderInfo:
        data = _MOCK_ORDERS.get(order_no.strip())
        if not data:
            logger.warning("order_service.order_not_found", order_no=order_no)
            raise OrderNotFoundError(f"订单号 {order_no} 不存在")

        # 归属校验（防 IDOR）：只能查自己的订单；对外统一返回「不存在」，避免单号枚举
        if customer_id and data["customer_id"] != customer_id:
            logger.warning(
                "order_service.order_forbidden",
                order_no=order_no,
                customer_id=customer_id,
            )
            raise OrderNotFoundError("订单不存在或无权查看")

        logger.info("order_service.lookup", order_no=order_no, status=data["status"])
        return OrderInfo(
            order_no=data["order_no"],
            customer_id=data["customer_id"],
            product_name=data["product_name"],
            amount=data["amount"],
            status=data["status"],
            logistics=data["logistics"],
            is_used=data["is_used"],
            is_in_warranty=data["is_in_warranty"],
            compensation=data["compensation"],
            raw=data,
        )


_order_service: Optional[OrderService] = None


def get_order_service() -> OrderService:
    """获取订单服务单例（默认 Mock；生产替换实现即可）。"""
    global _order_service
    if _order_service is None:
        _order_service = MockOrderService()
    return _order_service

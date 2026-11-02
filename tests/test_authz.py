# tests/test_authz.py
# P0 回归：坐席接口必须校验 role；客户不能审批退款/接管投诉；订单只能查自己的。

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.api.v1 import complaint as complaint_api
from backend.api.v1 import return_ as return_api
from backend.dependencies import require_agent
from backend.services.order_service import MockOrderService
from backend.core.exceptions import OrderNotFoundError


def test_require_agent_blocks_customer():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_agent({"user_id": "c1", "role": "customer", "tenant_id": "t"}))

    assert exc.value.status_code == 403


def test_require_agent_allows_agent():
    user = {"user_id": "agent-01", "role": "agent", "tenant_id": "t"}

    assert asyncio.run(require_agent(user)) == user


def _return_client() -> TestClient:
    app = FastAPI()
    app.include_router(return_api.router, prefix="/return")
    return TestClient(app)


def test_customer_cannot_list_pending_returns():
    resp = _return_client().get("/return/pending", headers={"X-Role": "customer"})

    assert resp.status_code == 403


def test_customer_cannot_confirm_return(monkeypatch):
    # 即使申请存在，客户也不能审批
    return_api._applications["app-authz-1"] = {
        "application_id": "app-authz-1",
        "order_no": "88001",
        "customer_id": "demo-customer",
        "reason": "test",
        "status": "pending_review",
        "thread_id": "thread-authz-1",
    }

    resp = _return_client().post(
        "/return/app-authz-1/confirm",
        json={"action": "approve"},
        headers={"X-Role": "customer", "X-Customer-Id": "demo-customer"},
    )

    assert resp.status_code == 403


def test_agent_cannot_confirm_already_finished_return():
    return_api._applications["app-authz-2"] = {
        "application_id": "app-authz-2",
        "order_no": "88001",
        "customer_id": "demo-customer",
        "reason": "test",
        "status": "done",
        "thread_id": "thread-authz-2",
    }

    resp = _return_client().post(
        "/return/app-authz-2/confirm",
        json={"action": "approve"},
        headers={"X-Role": "agent", "X-Customer-Id": "agent-01"},
    )

    assert resp.status_code == 409


def test_customer_cannot_resolve_complaint():
    app = FastAPI()
    app.include_router(complaint_api.router, prefix="/complaint")
    client = TestClient(app)

    resp = client.post(
        "/complaint/any-id/resolve",
        json={"note": "self approve"},
        headers={"X-Role": "customer"},
    )

    assert resp.status_code == 403


def test_order_lookup_enforces_ownership():
    service = MockOrderService()

    info = asyncio.run(service.lookup("88001", "demo-customer"))
    assert info.order_no == "88001"

    with pytest.raises(OrderNotFoundError):
        asyncio.run(service.lookup("88001", "another-customer"))

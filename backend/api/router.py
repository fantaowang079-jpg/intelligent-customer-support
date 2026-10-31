# backend/api/router.py
# API 路由总入口。

from fastapi import APIRouter
from backend.api.v1 import unified_chat, return_ as return_api, complaint

api_router = APIRouter()

api_router.include_router(unified_chat.router, prefix="/chat",      tags=["统一客服"])
api_router.include_router(return_api.router,   prefix="/return",    tags=["退换货"])
api_router.include_router(complaint.router,    prefix="/complaint", tags=["投诉升级"])

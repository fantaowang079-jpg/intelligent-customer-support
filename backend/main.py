# backend/main.py
# uvicorn backend.main:app --host 0.0.0.0 --port 8000

import os
import sys

# Windows conda 专用：把 base 的 Library/bin 加入 DLL 搜索路径
if sys.platform == "win32":
    _conda_base_lib_bin = os.path.normpath(
        os.path.join(os.path.dirname(sys.executable), "..", "..", "Library", "bin")
    )
    if os.path.isdir(_conda_base_lib_bin):
        os.add_dll_directory(_conda_base_lib_bin)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.core.logger import configure_logging, get_logger
from backend.api.router import api_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = get_logger(__name__)
    logger.info("app.starting | env=%s port=%s", settings.app_env, settings.app_port)

    # 并行预热本地模型（BGE-M3 + Reranker；不可用时优雅降级）
    import asyncio
    try:
        from backend.core.knowledge_store import LocalEmbedder, InMemoryKnowledgeStore
        from backend.core.reranker import BGEReranker

        loop = asyncio.get_running_loop()
        await asyncio.gather(
            loop.run_in_executor(None, LocalEmbedder.get_instance),
            loop.run_in_executor(None, InMemoryKnowledgeStore.get_instance),
            loop.run_in_executor(None, BGEReranker.get_instance),
        )
        logger.info("app.local_models_warmed_up")
    except Exception as e:
        logger.warning("app.local_models_warmup_failed | error=%s", e)

    logger.info("app.started")
    yield

    logger.info("app.shutting_down")
    from backend.core.llm_factory import LLMFactory
    LLMFactory.clear_cache()
    logger.info("app.shutdown_complete")


app = FastAPI(
    title="CustomerService Agent API",
    description="智能客服中台 API（多 Agent：意图路由 / FAQ-RAG / 订单状态机 / 退换货 HitL / 投诉升级）",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://localhost:5173", "http://localhost:8080",
        "http://127.0.0.1:3000", "http://127.0.0.1:5173", "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["系统"])
async def health_check():
    return {"status": "ok", "env": settings.app_env}

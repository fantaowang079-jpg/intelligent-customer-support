# backend/core/retry.py
# 三层兜底机制：自动重试 → Agent 级降级 → 系统级兜底

import asyncio
from functools import wraps
from typing import Callable, Any, Optional

from backend.core.exceptions import (
    LLMAPIError,
    InvalidInputError,
    AuthenticationError,
)
from backend.core.logger import get_logger

logger = get_logger(__name__)

# ── 异常分类 ─────────────────────────────────────────────────
RETRYABLE_ERRORS = (
    LLMAPIError,
    TimeoutError,
    ConnectionError,
)
NON_RETRYABLE_ERRORS = (
    InvalidInputError,
    AuthenticationError,
)

MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]
TIMEOUT_PER_ATTEMPT = 30.0


def with_retry(agent_type: str = ""):
    """三层兜底装饰器工厂。给异步函数套上「重试 → 降级 → 系统兜底」三层保护。"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:

            # ── 第一层：自动重试 ──────────────────────────
            last_error: Optional[Exception] = None
            for attempt in range(MAX_RETRIES + 1):
                try:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=TIMEOUT_PER_ATTEMPT,
                    )
                    if attempt > 0:
                        logger.info("retry.succeeded", agent_type=agent_type, attempt=attempt + 1)
                    return result

                except NON_RETRYABLE_ERRORS as e:
                    logger.warning("retry.non_retryable_error", agent_type=agent_type, error=str(e))
                    raise

                except Exception as e:
                    last_error = e
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[attempt]
                        logger.warning(
                            "retry.attempt_failed", agent_type=agent_type,
                            attempt=attempt + 1, max_retries=MAX_RETRIES, delay=delay, error=str(e),
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error("retry.all_attempts_failed", agent_type=agent_type, error=str(e))

            # ── 第二层：Agent 级降级 ──────────────────────
            try:
                fallback_result = await AgentFallbackHandler.handle(
                    agent_type=agent_type, original_error=last_error,
                )
                logger.info("retry.fallback_succeeded", agent_type=agent_type)
                return fallback_result
            except Exception as fallback_error:
                logger.error("retry.fallback_failed", agent_type=agent_type, error=str(fallback_error))

            # ── 第三层：系统级兜底 ────────────────────────
            logger.error("retry.system_fallback", agent_type=agent_type, original_error=str(last_error))
            return _system_fallback_response(agent_type)
        return wrapper
    return decorator


class AgentFallbackHandler:
    """第二层降级：各 Agent 的专项降级策略。"""

    @classmethod
    async def handle(cls, agent_type: str, original_error: Exception) -> Any:
        fallback_map = {
            "faq":       cls._faq_fallback,
            "order":     cls._order_fallback,
            "return":    cls._return_fallback,
            "complaint": cls._complaint_fallback,
            "intent":    cls._intent_fallback,
        }
        handler = fallback_map.get(agent_type)
        if handler:
            return await handler()
        raise original_error

    @classmethod
    async def _faq_fallback(cls) -> dict:
        logger.info("fallback.faq_unavailable")
        return {
            "fallback_used": True,
            "answer": "⚠️ 知识库检索暂时不可用，已为您转接人工客服，请稍候。",
            "answer_mode": "human_handoff",
            "sources": [],
            "structured_output": None,
            "messages": [],
        }

    @classmethod
    async def _order_fallback(cls) -> dict:
        logger.info("fallback.order_unavailable")
        return {
            "fallback_used": True,
            "reply": "⚠️ 订单查询服务暂时不可用，请稍后重试，或直接转人工客服为您查询。",
            "structured_output": None,
            "messages": [],
        }

    @classmethod
    async def _return_fallback(cls) -> dict:
        logger.info("fallback.return_unavailable")
        return {
            "fallback_used": True,
            "needs_human_review": True,
            "final_result": {"status": "failed"},
            "structured_output": {"status": "failed", "message": "退换货审核服务暂时不可用，已转人工处理。"},
        }

    @classmethod
    async def _complaint_fallback(cls) -> dict:
        logger.info("fallback.complaint_unavailable")
        return {
            "fallback_used": True,
            "reply": "非常抱歉给您带来不便，您的反馈已记录，人工客服将尽快与您联系。",
            "escalated": True,
            "structured_output": None,
            "messages": [],
        }

    @classmethod
    async def _intent_fallback(cls) -> dict:
        logger.info("fallback.intent_unavailable")
        return {
            "fallback_used": True,
            "intent": "human",
            "urgency": "normal",
            "route_to": "human",
            "reason": "路由服务异常，默认转人工。",
        }


def _system_fallback_response(agent_type: str) -> dict:
    """第三层：系统级兜底。所有降级都失败后返回它。"""
    messages = {
        "faq":       "非常抱歉，智能客服服务暂时不可用，请稍后再试，或直接致电人工客服。",
        "order":     "非常抱歉，订单查询服务暂时不可用，请稍后再试。",
        "return":    "非常抱歉，退换货服务暂时不可用，您的申请已保存，待服务恢复后将自动处理。",
        "complaint": "非常抱歉，投诉受理服务暂时不可用，人工客服将尽快与您联系。",
        "intent":    "非常抱歉，服务暂时不可用，已为您转接人工客服。",
    }
    content = messages.get(agent_type, "服务暂时不可用，请稍后再试。")
    return {
        "messages": [],
        "content": content,
        "fallback_used": True,
        "system_fallback": True,
        "structured_output": None,
    }

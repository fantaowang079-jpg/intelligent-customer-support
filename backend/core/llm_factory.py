# backend/core/llm_factory.py
# LLM Factory：统一封装大模型调用，按 Agent 类型路由。
# 规矩：所有 Agent 必须通过此模块获取模型，禁止直接调用 init_chat_model。

from typing import Type, Any
from pydantic import BaseModel
import httpx
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)

# ── 自定义 httpx 客户端：绕过系统代理（DeepSeek 国内可直连，无需代理）──
_HTTP_ASYNC_CLIENT = httpx.AsyncClient(
    trust_env=False,
    timeout=httpx.Timeout(120.0, connect=15.0),
)
_HTTP_SYNC_CLIENT = httpx.Client(
    trust_env=False,
    timeout=httpx.Timeout(120.0, connect=15.0),
)

# ── Agent 类型 → 模型标识符 的路由表 ────────────────────────────
_AGENT_MODEL_ROUTING: dict[str, str] = {
    "faq":       "deepseek-chat",   # FAQ 问答（RAG）
    "order":     "deepseek-chat",   # 订单状态（状态机）
    "return":    "deepseek-chat",   # 退换货（HitL）
    "complaint": "deepseek-chat",   # 投诉升级（HitL）
    "intent":    "deepseek-chat",   # 意图识别 / 路由
    "summarize": "deepseek-chat",   # 对话摘要压缩
}

# ── 模型标识符 → DeepSeek API 实际 model 名称 ────────────────────
_MODEL_ID_MAP: dict[str, str] = {
    "deepseek-chat": "deepseek-v4-flash",
}


class LLMFactory:
    """大模型工厂（统一获取模型的唯一入口）。"""

    _instances: dict[str, BaseChatModel] = {}

    @classmethod
    def _get_settings(cls):
        return get_settings()

    @classmethod
    def _build_model_kwargs(cls, model_key: str) -> dict[str, Any]:
        settings = cls._get_settings()
        model_id = _MODEL_ID_MAP[model_key]

        return {
            "model": model_id,
            "model_provider": "openai",
            "temperature": 0,
            "api_key": settings.deepseek_api_key,
            "base_url": settings.deepseek_base_url,
            "max_retries": 0,                       # 重试统一由 retry.py 管
            "http_async_client": _HTTP_ASYNC_CLIENT,
            "http_client": _HTTP_SYNC_CLIENT,
        }

    @classmethod
    def get_llm(
        cls,
        agent_type: str,
        temperature: float = 0,
        streaming: bool = False,
    ) -> BaseChatModel:
        """按 Agent 类型获取模型实例（带缓存）。"""
        if agent_type not in _AGENT_MODEL_ROUTING:
            raise ValueError(
                f"未知 agent_type: '{agent_type}'，"
                f"可用类型：{list(_AGENT_MODEL_ROUTING.keys())}"
            )
        model_key = _AGENT_MODEL_ROUTING[agent_type]
        cache_key = f"{model_key}_{temperature}_{streaming}"
        if cache_key not in cls._instances:
            kwargs = cls._build_model_kwargs(model_key)
            kwargs["temperature"] = temperature
            kwargs["streaming"] = streaming
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            llm = init_chat_model(**kwargs)
            cls._instances[cache_key] = llm
            logger.info(
                "llm_factory.model_initialized",
                agent_type=agent_type, model_key=model_key,
                temperature=temperature, streaming=streaming,
            )
        return cls._instances[cache_key]

    @classmethod
    def get_structured_llm(
        cls,
        agent_type: str,
        output_schema: Type[BaseModel],
        temperature: float = 0,
    ) -> Runnable:
        """获取「绑定了结构化输出 Schema」的模型。"""
        llm = cls.get_llm(agent_type, temperature=temperature)
        return llm.with_structured_output(output_schema, method="function_calling")

    @classmethod
    def clear_cache(cls) -> None:
        cls._instances.clear()
        logger.info("llm_factory.cache_cleared")


# ── 模块级便捷函数 ────────────────────────────────────────────

def get_llm(agent_type: str, temperature: float = 0, streaming: bool = False) -> BaseChatModel:
    return LLMFactory.get_llm(agent_type, temperature=temperature, streaming=streaming)


def get_structured_llm(agent_type: str, output_schema: Type[BaseModel]) -> Runnable:
    return LLMFactory.get_structured_llm(agent_type, output_schema)

# backend/core/memory.py
# MemorySaver 对话记忆 + 会话摘要压缩。

from typing import Optional
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from backend.core.logger import get_logger

logger = get_logger(__name__)

# 每个 Agent 独立的 MemorySaver 实例（不同 State schema 必须隔离）
_memory_savers: dict[str, MemorySaver] = {}


def get_memory_saver(agent_type: str = "default") -> MemorySaver:
    """
    获取指定 Agent 类型的 MemorySaver 单例。

    本地阶段使用内存存储；生产阶段替换为持久化 Saver 即可，业务代码无需修改。
    """
    if agent_type not in _memory_savers:
        _memory_savers[agent_type] = MemorySaver()
        logger.info("memory.saver_initialized", agent=agent_type)
    return _memory_savers[agent_type]


def build_thread_id(customer_id: str, session_id: str) -> str:
    """
    构建 LangGraph Checkpointer 使用的 thread_id。

    格式：customer_{customer_id}_session_{session_id}
    """
    return f"customer_{customer_id}_session_{session_id}"


def build_config(customer_id: str, session_id: str) -> dict:
    """
    构建 LangGraph 调用所需的 config 字典。
    """
    return {
        "configurable": {
            "thread_id": build_thread_id(customer_id, session_id),
        }
    }


def trim_messages_to_window(
    messages: list[BaseMessage],
    window_size: int = 10,
) -> list[BaseMessage]:
    """
    滑动窗口裁剪：保留最近 window_size 轮对话。SystemMessage 始终保留在最前。
    """
    system_messages = [m for m in messages if isinstance(m, SystemMessage)]
    dialogue_messages = [m for m in messages if not isinstance(m, SystemMessage)]
    max_dialogue_messages = window_size * 2

    if len(dialogue_messages) <= max_dialogue_messages:
        return messages

    trimmed = dialogue_messages[-max_dialogue_messages:]

    logger.info(
        "memory.window_trimmed",
        original=len(dialogue_messages),
        kept=len(trimmed),
        window_size=window_size,
    )

    return system_messages + trimmed


def should_trigger_summary(
    messages: list[BaseMessage],
    threshold: int = 10,
) -> bool:
    """判断对话轮数是否超过阈值，决定是否触发摘要压缩。"""
    dialogue_count = sum(1 for m in messages if isinstance(m, (HumanMessage, AIMessage)))
    rounds = dialogue_count // 2
    return rounds >= threshold


async def compress_to_summary(
    messages: list[BaseMessage],
    existing_summary: Optional[str] = None,
) -> str:
    """
    将历史对话压缩为结构化客户画像摘要（增量压缩）。
    """
    from backend.core.llm_factory import get_llm

    SUMMARY_PROMPT = """请将以下客户对话压缩为结构化客户画像摘要。

【压缩规则】
必须保留：客户明确提出的诉求 / 反复出现的问题 / 已提供的关键信息（订单号、联系方式、商品）
选择性保留：已解决的诉求（简短标注）/ 客户偏好
可以丢弃：已在上次摘要记录的内容 / 闲聊 / 已完全解决的问题

【上一次摘要】
{previous_summary}

【本次新增对话】
{new_conversations}

请直接输出摘要文本，不要加任何前缀。"""

    conversation_text = "\n".join([
        f"{'客户' if isinstance(m, HumanMessage) else '客服'}：{m.text if hasattr(m, 'text') and not callable(m.text) else str(m.content)}"
        for m in messages
        if isinstance(m, (HumanMessage, AIMessage))
    ])

    prompt_text = SUMMARY_PROMPT.format(
        previous_summary=existing_summary or "（无上次摘要）",
        new_conversations=conversation_text,
    )

    llm = get_llm("summarize")
    response = await llm.ainvoke([HumanMessage(content=prompt_text)])
    summary = (
        response.content if isinstance(response.content, str)
        else str(response.content)
    ).strip()

    logger.info(
        "memory.summary_generated",
        input_messages=len(messages),
        summary_length=len(summary),
    )
    return summary

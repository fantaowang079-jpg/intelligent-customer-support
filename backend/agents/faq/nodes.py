# backend/agents/faq/nodes.py

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from backend.agents.faq.state import FAQState
from backend.agents.faq.prompts import SYSTEM_PROMPT, RAG_ANSWER_PROMPT
from backend.core.llm_factory import get_llm
from backend.core.logger import get_logger
from backend.core.reranker import retrieve, HIGH_CONFIDENCE_THRESHOLD

logger = get_logger(__name__)


def _get_message_content(msg) -> str:
    """统一获取消息文本（兼容 .text 和 .content）。"""
    if hasattr(msg, "text") and not callable(getattr(msg, "text", None)):
        return msg.text
    if isinstance(msg.content, str):
        return msg.content
    return str(msg.content)


def _extract_query(state: FAQState) -> str:
    """取最后一条 HumanMessage 作为原始输入。"""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return _get_message_content(msg)
    return ""


def _recent_dialogue(state: FAQState, max_messages: int = 4, max_chars: int = 600) -> str:
    """最近几轮对话（不含当前问题），用于「那运费谁出？」这类追问的指代消解。"""
    convo = [m for m in state.get("messages", []) if isinstance(m, (HumanMessage, AIMessage))]
    if convo and isinstance(convo[-1], HumanMessage):
        convo = convo[:-1]  # 最后一条是当前问题，单独放在【客户问题】里

    lines: list[str] = []
    used = 0
    for msg in reversed(convo[-max_messages:]):
        role = "客户" if isinstance(msg, HumanMessage) else "客服"
        text = " ".join(_get_message_content(msg).split())
        if not text:
            continue
        line = f"{role}：{text[:200]}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(reversed(lines))


async def retrieve_node(state: FAQState) -> dict:
    """
    检索节点：内存向量库召回 →（可选）BGE 精排，产出置信度。
    多轮会话必须以「本轮最新问题」为检索词，否则会一直复用第一问。
    """
    query = _extract_query(state) or state.get("original_query", "")

    # retrieve() 是同步阻塞（本地模型 CPU 推理），放入线程池避免阻塞事件循环
    import asyncio
    loop = asyncio.get_running_loop()
    ranked_docs, confidence = await loop.run_in_executor(
        None, lambda: retrieve(query, recall_top_k=8, rerank_top_k=3)
    )

    ranked_chunks = [
        {
            "content": doc.content,
            "score": doc.score,
            "metadata": doc.metadata,
        }
        for doc in ranked_docs
    ]

    is_high = confidence >= HIGH_CONFIDENCE_THRESHOLD

    logger.info(
        "faq.retrieve_done",
        query=query[:40],
        ranked=len(ranked_chunks),
        confidence=round(confidence, 4),
        is_high_confidence=is_high,
    )

    return {
        "original_query": query,
        "ranked_chunks": ranked_chunks,
        "confidence": confidence,
        "is_high_confidence": is_high,
    }


def _route_by_confidence(state: FAQState) -> str:
    """retrieve 之后的路由：高置信度走 RAG，低置信度转人工。"""
    return "high" if state.get("is_high_confidence", False) else "low"


async def generate_rag_node(state: FAQState) -> dict:
    """高置信度 RAG 生成：严格基于知识库回答，附引用来源。"""
    ranked_chunks = state.get("ranked_chunks", [])
    query = _extract_query(state) or state.get("original_query", "")
    history = _recent_dialogue(state)

    context_parts = []
    sources = []
    for i, chunk in enumerate(ranked_chunks, 1):
        context_parts.append(f"【参考{i}】\n{chunk['content']}")
        source_name = chunk.get("metadata", {}).get("source_name", "知识库")
        if source_name not in sources:
            sources.append(source_name)

    context_text = "\n\n".join(context_parts) or "（无参考内容）"

    prompt = RAG_ANSWER_PROMPT.format(
        context=context_text,
        history=history or "（无）",
        query=query,
    )
    llm = get_llm("faq", streaming=True)
    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    answer_text = _get_message_content(response).strip()

    sources_text = "\n".join([f"  • {s}" for s in sources])
    final_answer = f"{answer_text}\n\n📚 **参考来源**\n{sources_text}"

    logger.info("faq.generate_rag_done", answer_length=len(final_answer), sources=sources)

    return {
        "answer": final_answer,
        "sources": sources,
        "answer_mode": "rag",
        "messages": [AIMessage(content=final_answer)],
        "structured_output": {
            "answer": final_answer,
            "sources": sources,
            "confidence": state.get("confidence", 0),
            "answer_mode": "rag",
        },
    }


async def escalate_human_node(state: FAQState) -> dict:
    """低置信度节点：不编造，转人工。"""
    from backend.services.after_sale_service import get_after_sale_service

    query = _extract_query(state) or state.get("original_query", "")
    reply = (
        "抱歉，关于这个问题我暂时没有足够可靠的依据，为避免给您错误信息，"
        "已为您转接人工客服，请稍候。"
    )

    # 落审计：低置信度问题记录为待人工工单
    try:
        await get_after_sale_service().log({
            "type": "faq_handoff",
            "query": query,
            "confidence": state.get("confidence", 0.0),
        })
    except Exception as e:
        logger.warning("faq.escalate_log_failed", error=str(e))

    logger.info("faq.escalate_human", query=query[:40])

    return {
        "answer": reply,
        "sources": [],
        "answer_mode": "human_handoff",
        "messages": [AIMessage(content=reply)],
        "structured_output": {
            "answer": reply,
            "sources": [],
            "confidence": state.get("confidence", 0),
            "answer_mode": "human_handoff",
        },
    }

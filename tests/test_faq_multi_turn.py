# tests/test_faq_multi_turn.py
# P0 回归：多轮 FAQ 必须用「本轮最新问题」检索；追问要能带上最近对话。

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from backend.agents.faq import nodes as faq_nodes


def test_second_turn_uses_latest_question(monkeypatch):
    captured = {}

    def fake_retrieve(query, **kwargs):
        captured["query"] = query
        return [], 0.9

    monkeypatch.setattr(faq_nodes, "retrieve", fake_retrieve)

    state = {
        "messages": [
            HumanMessage(content="退换货的运费谁承担？"),
            AIMessage(content="质量问题由商家承担……"),
            HumanMessage(content="那换货运费呢？"),
        ],
        # 上一轮写入 checkpointer 的旧 query，不能被复用
        "original_query": "退换货的运费谁承担？",
    }

    asyncio.run(faq_nodes.retrieve_node(state))

    assert captured["query"] == "那换货运费呢？"


def test_recent_dialogue_excludes_current_question():
    state = {
        "messages": [
            HumanMessage(content="退换货的运费谁承担？"),
            AIMessage(content="质量问题由商家承担……"),
            HumanMessage(content="那换货运费呢？"),
        ],
    }

    history = faq_nodes._recent_dialogue(state)

    assert "退换货的运费谁承担" in history
    assert "那换货运费呢" not in history


def test_rag_prompt_includes_history(monkeypatch):
    captured = {}

    class FakeLLM:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="换货的运费同样由商家承担。")

    monkeypatch.setattr(faq_nodes, "get_llm", lambda *a, **k: FakeLLM())

    state = {
        "messages": [
            HumanMessage(content="退换货的运费谁承担？"),
            AIMessage(content="质量问题由商家承担……"),
            HumanMessage(content="那换货运费呢？"),
        ],
        "original_query": "退换货的运费谁承担？",
        "ranked_chunks": [
            {"content": "质量问题导致的退换货，运费由商家承担。", "score": 0.9, "metadata": {"source_name": "售后政策"}},
        ],
        "confidence": 0.9,
    }

    asyncio.run(faq_nodes.generate_rag_node(state))

    prompt = captured["messages"][-1].content
    assert "退换货的运费谁承担" in prompt  # 历史用于指代消解
    assert "那换货运费呢" in prompt        # 当前问题

# backend/core/reranker.py
# BGE-Reranker 精排 + retrieve() 一体化 Pipeline（底层改为内存向量库）。

import os
from dataclasses import dataclass
from typing import Optional

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)
_backend_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RERANK_MAX_INPUT_CHARS = 512


@dataclass
class RankedDocument:
    """精排后的单个文档结果"""
    content:        str
    score:          float
    original_index: int
    metadata:       dict


# 高置信度阈值：召回/精排置信度 >= 该值走 RAG 生成，否则转人工
# 0.5 同时兼顾精排概率（BGE 精排：相关 >0.6 / 无关 <0.2）与关键词降级得分
HIGH_CONFIDENCE_THRESHOLD = 0.5


class BGEReranker:
    """BGE-Reranker 精排服务（单例）。可用时输出 [0,1] 概率，不可用则由上层降级。"""

    _instance: Optional["BGEReranker"] = None

    def __init__(self):
        settings = get_settings()
        model_path = os.path.join(_backend_path, settings.reranker_model_path)

        use_local = (
            os.path.exists(model_path)
            and os.path.isdir(model_path)
            and any(f.endswith((".bin", ".safetensors", ".json")) for f in os.listdir(model_path))
        )
        model_id = model_path if use_local else "BAAI/bge-reranker-v2-m3"

        self._model = None
        try:
            import torch
            from sentence_transformers import CrossEncoder
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = CrossEncoder(model_id, device=device, max_length=512)
            logger.info("reranker.loaded", model_id=model_id, device=device)
        except Exception as e:
            logger.warning("reranker.load_failed", error=str(e))

    @classmethod
    def get_instance(cls) -> "BGEReranker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def available(self) -> bool:
        return self._model is not None

    def rerank_with_confidence(
        self,
        query: str,
        documents: list[dict],
        top_k: int = 3,
    ) -> tuple[list[RankedDocument], float]:
        if not documents:
            return [], 0.0

        pairs = [
            (query, (doc.get("content") or "")[:RERANK_MAX_INPUT_CHARS])
            for doc in documents
        ]
        scores: list[float] = self._model.predict(pairs).tolist()
        ranked = sorted(
            [
                RankedDocument(
                    content=documents[i].get("content", ""),
                    score=scores[i],
                    original_index=i,
                    metadata=documents[i].get("metadata", {}),
                )
                for i in range(len(documents))
            ],
            key=lambda x: x.score,
            reverse=True,
        )
        top_results = ranked[:top_k]
        confidence = top_results[0].score if top_results else 0.0

        logger.info(
            "reranker.done",
            candidates=len(documents),
            top_k=top_k,
            confidence=round(confidence, 4),
        )
        return top_results, confidence


def retrieve(
    query: str,
    recall_top_k: int = 8,
    rerank_top_k: int = 3,
) -> tuple[list[RankedDocument], float]:
    """
    检索 → 精排一体化 Pipeline：内存向量库召回 +（可选）BGE 精排。

    Returns:
        (ranked_docs, confidence)
        confidence 为 [0, 1]：精排概率，或（精排不可用时）向量/关键词得分。
    """
    from backend.core.knowledge_store import InMemoryKnowledgeStore

    store = InMemoryKnowledgeStore.get_instance()
    candidates = store.search(query, top_k=recall_top_k)
    if not candidates:
        logger.info("retrieve.empty", query_preview=query[:40])
        return [], 0.0

    try:
        reranker = BGEReranker.get_instance()
        if reranker.available:
            return reranker.rerank_with_confidence(query, candidates, top_k=rerank_top_k)
    except Exception as e:
        logger.warning("retrieve.rerank_failed", error=str(e))

    # 精排不可用 → 直接用召回分
    top = candidates[:rerank_top_k]
    ranked = [
        RankedDocument(
            content=c["content"],
            score=c["score"],
            original_index=i,
            metadata=c["metadata"],
        )
        for i, c in enumerate(top)
    ]
    confidence = ranked[0].score if ranked else 0.0
    return ranked, confidence

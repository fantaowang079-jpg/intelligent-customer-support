# backend/core/knowledge_store.py
# 内存向量库：替代 Milvus。进程内 BGE-M3 嵌入 + 余弦相似度检索。
# 若本地嵌入模型不可用，自动降级为关键词（bigram 重叠）检索，保证演示不因缺模型而崩溃。

import os
import re
from typing import Optional

import numpy as np

from backend.config import get_settings
from backend.core.logger import get_logger
from backend.services.knowledge_seed import SEED_DOCUMENTS

logger = get_logger(__name__)

_backend_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 本地嵌入模型（BGE-M3，进程内单例，可失败降级）────────────────

class LocalEmbedder:
    """BGE-M3 本地嵌入模型单例。加载失败时标记不可用，走关键词降级。"""

    _instance: Optional["LocalEmbedder"] = None
    _available: bool = False

    def __init__(self, model_path: str):
        self._model = None
        try:
            import torch
            from FlagEmbedding import BGEM3FlagModel
            _use_fp16 = torch.cuda.is_available()
            self._model = BGEM3FlagModel(model_name_or_path=model_path, use_fp16=_use_fp16)
            LocalEmbedder._available = True
            logger.info("embedder.loaded", model_path=model_path)
        except Exception as e:
            LocalEmbedder._available = False
            logger.warning("embedder.load_failed", error=str(e))

    @classmethod
    def get_instance(cls) -> "LocalEmbedder":
        if cls._instance is None:
            model_path = os.path.join(_backend_path, get_settings().bge_m3_model_path)
            cls._instance = cls(model_path)
        return cls._instance

    @property
    def available(self) -> bool:
        return LocalEmbedder._available

    def encode(self, texts: list[str]) -> list[list[float]]:
        output = self._model.encode(
            texts,
            batch_size=8,
            max_length=8192,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return output["dense_vecs"].tolist()

    def encode_query(self, text: str) -> list[float]:
        return self.encode([text])[0]


# ── 工具：文本 ngram 切分（中文 bigram / 英文按词）────────────────

def _ngrams(text: str) -> set:
    t = text.lower()
    if t.isascii():
        return set(re.findall(r"[a-z0-9]+", t))
    return set(t[i:i + 2] for i in range(len(t) - 1))


def _keyword_score(query: str, content: str) -> float:
    """关键词重叠得分，归一化到 [0, 1]。"""
    q_ng = _ngrams(query)
    c_ng = _ngrams(content)
    if not q_ng:
        return 0.0
    return len(q_ng & c_ng) / len(q_ng)


# ── 内存向量库 ────────────────────────────────────────────────

class InMemoryKnowledgeStore:
    """内存知识库：启动时嵌入种子文档，检索时余弦相似度 Top-K。"""

    _instance: Optional["InMemoryKnowledgeStore"] = None

    def __init__(self):
        self._docs = [
            {"content": d["content"], "source_name": d["source_name"]}
            for d in SEED_DOCUMENTS
        ]
        self._vectors: Optional[np.ndarray] = None
        self._norm: Optional[np.ndarray] = None

        embedder = LocalEmbedder.get_instance()
        if embedder.available:
            try:
                vecs = embedder.encode([d["content"] for d in self._docs])
                self._vectors = np.array(vecs, dtype=np.float32)
                self._norm = np.linalg.norm(self._vectors, axis=1, keepdims=True)
                self._norm[self._norm == 0] = 1.0
                logger.info("knowledge_store.embedded", docs=len(self._docs))
            except Exception as e:
                logger.warning("knowledge_store.embed_failed", error=str(e))

        if self._vectors is None:
            logger.warning("knowledge_store.keyword_fallback", docs=len(self._docs))

    @classmethod
    def get_instance(cls) -> "InMemoryKnowledgeStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search(self, query: str, top_k: int = 8) -> list[dict]:
        """
        检索 Top-K 文档，返回 list[dict]，每项含 content / score / metadata。
        score 已归一化到 [0, 1]。
        """
        if self._vectors is not None:
            scores = self._embed_search(query)
        else:
            scores = [float(_keyword_score(query, d["content"])) for d in self._docs]

        ranked = sorted(
            [
                {
                    "content": self._docs[i]["content"],
                    "score": max(0.0, min(1.0, float(scores[i]))),
                    "metadata": {"source_name": self._docs[i]["source_name"]},
                }
                for i in range(len(self._docs))
            ],
            key=lambda x: x["score"],
            reverse=True,
        )
        top = ranked[:top_k]
        logger.info("knowledge_store.search_done", query=query[:40], top=len(top))
        return top

    def _embed_search(self, query: str) -> list[float]:
        embedder = LocalEmbedder.get_instance()
        qv = np.array(embedder.encode_query(query), dtype=np.float32)
        qv_norm = np.linalg.norm(qv)
        if qv_norm == 0:
            return [0.0] * len(self._docs)
        sim = (self._vectors @ qv) / (self._norm.ravel() * qv_norm)
        return sim.tolist()

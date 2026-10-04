from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=4)
def _load_cross_encoder(model_name: str):
    if not model_name:
        return None
    try:
        from sentence_transformers import CrossEncoder
        return CrossEncoder(model_name)
    except Exception:
        return None


class Reranker:
    def __init__(self, model_name: str = "", enabled: bool = True):
        self.model = _load_cross_encoder(model_name) if enabled else None

    @property
    def backend(self) -> str:
        return "cross_encoder" if self.model is not None else "lexical_fallback"

    def rerank(self, query: str, results: list, k: int = 5):
        if not results:
            return []
        if self.model:
            scores = self.model.predict([(query, r.text) for r in results])
            for r, score in zip(results, scores):
                r.rerank_score = float(score)
        else:
            q = set(re.findall(r"[a-z0-9]+", query.lower()))
            for r in results:
                toks = set(re.findall(r"[a-z0-9]+", r.text.lower()))
                overlap = len(q.intersection(toks)) / max(len(q), 1)
                service_boost = 0.1 if r.metadata.get("service", "").lower() in query.lower() else 0.0
                r.rerank_score = 0.6 * r.score + 0.35 * overlap + service_boost
        results.sort(key=lambda x: x.rerank_score, reverse=True)
        return results[:k]

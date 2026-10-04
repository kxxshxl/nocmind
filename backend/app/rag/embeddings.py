from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Iterable


class LocalEmbedding:
    """Deterministic fallback embedding used only when the semantic backend is unavailable."""
    def __init__(self, dimensions: int = 384): self.dimensions = dimensions
    def encode(self, texts: Iterable[str]) -> list[list[float]]: return [self._one(t) for t in texts]
    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions; clean = text.lower()
        tokens = re.findall(r"[a-z0-9_/-]+", clean)
        grams = tokens + [clean[i:i+3] for i in range(max(0, len(clean)-2))]
        for item in grams:
            h = hashlib.blake2b(item.encode(), digest_size=8).digest(); idx = int.from_bytes(h, 'big') % self.dimensions; vec[idx] += 1.0 if h[0] % 2 == 0 else -1.0
        norm = math.sqrt(sum(x*x for x in vec)) or 1.0
        return [x / norm for x in vec]


class SentenceTransformerEmbedding:
    def __init__(self, model_name: str = 'all-MiniLM-L6-v2'):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        get_dim = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dimensions = int(get_dim())
    def encode(self, texts: Iterable[str]) -> list[list[float]]:
        return self.model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False).tolist()


@lru_cache(maxsize=4)
def build_embedder(backend: str, model_name: str, dimensions: int):
    if backend.lower() in {"sentence_transformer", "sentence-transformers", "semantic"}:
        try: return SentenceTransformerEmbedding(model_name)
        except Exception: return LocalEmbedding(dimensions)
    return LocalEmbedding(dimensions)

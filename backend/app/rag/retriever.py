from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DocumentChunk
from app.rag.embeddings import build_embedder


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    text: str
    section: str
    metadata: dict
    score: float
    dense_score: float = 0.0
    lexical_score: float = 0.0
    rerank_score: float = 0.0


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(x * x for x in b)) or 1))


class HybridRetriever:
    def __init__(self, db: Session, embedding_dim: int | None = None, embedder=None):
        self.db = db
        self.embedder = embedder or build_embedder(settings.embedding_backend, settings.embedding_model, embedding_dim or settings.embedding_dim)

    def _lexical(self, query: str, text_value: str) -> float:
        q = set(re.findall(r"[a-z0-9]+", query.lower()))
        t = re.findall(r"[a-z0-9]+", text_value.lower())
        if not q or not t:
            return 0.0
        counts = {x: t.count(x) for x in q}
        return sum(min(c, 3) for c in counts.values()) / max(len(q), 1)

    def _postgres_lexical(self, query: str, candidate_ids: set[str], limit: int) -> list[tuple[DocumentChunk, float]]:
        if not candidate_ids:
            return []
        # PostgreSQL full-text retrieval; SQLAlchemy expands the candidate list safely.
        try:
            rows = self.db.execute(text('''
                SELECT c.id, c.document_id, c.text, c.section, c.metadata_json,
                       ts_rank_cd(to_tsvector('english', c.text), websearch_to_tsquery('english', :q)) AS score
                FROM document_chunks c
                WHERE to_tsvector('english', c.text) @@ websearch_to_tsquery('english', :q)
                ORDER BY score DESC
                LIMIT :lim
            '''), {"q": query, "lim": limit}).mappings().all()
            by_id = {r.id: r for r in self.db.query(DocumentChunk).filter(DocumentChunk.id.in_(candidate_ids)).all()}
            return [(by_id[str(r['id'])], float(r['score'])) for r in rows if str(r['id']) in by_id]
        except Exception:
            return []

    def search(self, query: str, top_k: int = 8) -> list[RetrievalResult]:
        rows = self.db.query(DocumentChunk).all()
        if not rows:
            return []
        qv = self.embedder.encode([query])[0]
        dense: list[tuple[DocumentChunk, float]] = []
        lexical: list[tuple[DocumentChunk, float]] = []
        candidate_k = max(top_k * 4, 20)

        pg_dense: dict[str, float] | None = None
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            try:
                from app.rag.pgvector_store import PgVectorStore
                pg_rows = PgVectorStore(self.db, getattr(self.embedder, "dimensions", settings.embedding_dim)).search(qv, candidate_k)
                pg_dense = {str(r["id"]): float(r["score"]) for r in pg_rows}
            except Exception:
                pg_dense = None

        for row in rows:
            try:
                embedding = json.loads(row.embedding_json or "[]")
            except json.JSONDecodeError:
                embedding = []
            dense.append((row, pg_dense.get(row.id, 0.0) if pg_dense is not None else cosine(qv, embedding)))

        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            pg_lexical = self._postgres_lexical(query, {r.id for r, _ in dense}, candidate_k)
            lexical = pg_lexical or [(row, self._lexical(query, row.text)) for row in rows]
        else:
            lexical = [(row, self._lexical(query, row.text)) for row in rows]

        dense.sort(key=lambda x: x[1], reverse=True)
        lexical.sort(key=lambda x: x[1], reverse=True)
        d_rank = {r.id: i + 1 for i, (r, _) in enumerate(dense[:candidate_k])}
        l_rank = {r.id: i + 1 for i, (r, _) in enumerate(lexical[:candidate_k])}
        pool = {r.id: r for r, _ in dense[:candidate_k]}
        pool.update({r.id: r for r, _ in lexical[:candidate_k]})
        dense_map = {r.id: score for r, score in dense}
        lexical_map = {r.id: score for r, score in lexical}
        results = []
        for cid, row in pool.items():
            rrf = 1 / (60 + d_rank.get(cid, 10_000)) + 1 / (60 + l_rank.get(cid, 10_000))
            results.append(RetrievalResult(
                chunk_id=cid, document_id=row.document_id, text=row.text, section=row.section,
                metadata=json.loads(row.metadata_json or "{}"), score=rrf,
                dense_score=dense_map.get(cid, 0.0), lexical_score=lexical_map.get(cid, 0.0),
            ))
        return sorted(results, key=lambda x: x.score, reverse=True)[:top_k]

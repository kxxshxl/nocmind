from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.cache import QueryCache
from app.core.config import settings
from app.core.telemetry import RAG_LATENCY, RAG_QUERIES, span
from app.db.models import Document, DocumentChunk
from app.rag.chunking import structure_aware_chunks
from app.rag.embeddings import build_embedder
from app.rag.reranker import Reranker
from app.rag.retriever import HybridRetriever


class RAGPipeline:
    def __init__(self, db: Session):
        self.db = db
        self.embedder = build_embedder(settings.embedding_backend, settings.embedding_model, settings.embedding_dim)
        self.retriever = HybridRetriever(db, getattr(self.embedder, "dimensions", settings.embedding_dim), embedder=self.embedder)
        self.reranker = Reranker(settings.reranker_model)
        self.cache = QueryCache()

    def ingest_document(self, document_id: str, title: str, kind: str, service: str, severity: str, version: str, text: str) -> int:
        doc = self.db.get(Document, document_id)
        if not doc:
            doc = Document(id=document_id, title=title, kind=kind, service=service, severity=severity, version=version)
            self.db.add(doc)
        else:
            self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
            doc.title, doc.kind, doc.service, doc.severity, doc.version = title, kind, service, severity, version
        chunks = structure_aware_chunks(text)
        vectors = self.embedder.encode([c.text for c in chunks])
        created_ids = []
        for idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
            cid = f"{document_id}:{idx}:{uuid.uuid4().hex[:6]}"
            created_ids.append(cid)
            self.db.add(DocumentChunk(
                id=cid,
                document_id=document_id,
                text=chunk.text,
                section=chunk.section,
                metadata_json=json.dumps({"title": title, "service": service, "severity": severity, "kind": kind, "version": version}),
                embedding_json=json.dumps(vec),
            ))
        self.db.commit()
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            try:
                from app.rag.pgvector_store import PgVectorStore
                store = PgVectorStore(self.db, getattr(self.embedder, "dimensions", settings.embedding_dim))
                store.ensure()
                for row_id, vec in zip(created_ids, vectors):
                    store.upsert(row_id, vec)
                self.db.commit()
            except Exception:
                self.db.rollback()
        return len(chunks)

    def query(self, query: str, top_k: int | None = None, rerank_k: int | None = None):
        started = time.perf_counter()
        RAG_QUERIES.inc()
        cache_key = json.dumps({"q": query, "top_k": top_k or settings.top_k, "rerank_k": rerank_k or settings.rerank_k}, sort_keys=True)
        cached = self.cache.get("rag", cache_key)
        if cached is not None:
            from app.rag.retriever import RetrievalResult
            result = [RetrievalResult(**row) for row in cached]
            RAG_LATENCY.observe(time.perf_counter() - started)
            return result
        with span("rag.query", {"rag.top_k": top_k or settings.top_k, "rag.reranker": self.reranker.backend}):
            retrieved = self.retriever.search(query, top_k or settings.top_k)
            result = self.reranker.rerank(query, retrieved, rerank_k or settings.rerank_k)
        self.cache.set("rag", cache_key, [r.__dict__ for r in result], ttl=300)
        RAG_LATENCY.observe(time.perf_counter() - started)
        return result

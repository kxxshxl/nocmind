from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


class PgVectorStore:
    def __init__(self, db: Session, dimensions: int):
        self.db = db
        self.dimensions = dimensions

    def ensure(self):
        self.db.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        self.db.execute(text(f'''CREATE TABLE IF NOT EXISTS document_vectors (\n            chunk_id VARCHAR(128) PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,\n            embedding vector({self.dimensions}) NOT NULL\n        )'''))
        self.db.commit()

    def upsert(self, chunk_id: str, vector: list[float]):
        value = '[' + ','.join(f'{float(x):.8f}' for x in vector) + ']'
        self.db.execute(text('''INSERT INTO document_vectors(chunk_id, embedding) VALUES (:id, CAST(:v AS vector))\n            ON CONFLICT(chunk_id) DO UPDATE SET embedding=EXCLUDED.embedding'''), {'id': chunk_id, 'v': value})

    def search(self, vector: list[float], limit: int = 20):
        value = '[' + ','.join(f'{float(x):.8f}' for x in vector) + ']'
        rows = self.db.execute(text('''SELECT c.id, c.document_id, c.text, c.section, c.metadata_json,\n               1 - (v.embedding <=> CAST(:v AS vector)) AS score\n            FROM document_vectors v JOIN document_chunks c ON c.id=v.chunk_id\n            ORDER BY v.embedding <=> CAST(:v AS vector) LIMIT :lim'''), {'v': value, 'lim': limit}).mappings().all()
        return rows

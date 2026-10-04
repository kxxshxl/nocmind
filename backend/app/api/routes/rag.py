from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import current_user
from app.db.models import User
from app.db.session import get_db
from app.rag.pipeline import RAGPipeline
from app.schemas.models import RAGQuery

router = APIRouter(prefix='/rag', tags=['rag'])

@router.post('/query')
def query(payload: RAGQuery, db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = RAGPipeline(db).query(payload.query, payload.top_k, payload.rerank_k)
    return [{'chunk_id': r.chunk_id, 'document_id': r.document_id, 'text': r.text, 'section': r.section,
             'score': r.score, 'dense_score': r.dense_score, 'lexical_score': r.lexical_score, 'rerank_score': r.rerank_score,
             'metadata': r.metadata} for r in rows]

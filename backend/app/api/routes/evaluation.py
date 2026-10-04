import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import audit, current_user, require_roles
from app.db.models import EvaluationRun, User
from app.db.session import get_db
from app.evaluation.run_benchmark import run_async

router = APIRouter(prefix='/evaluations', tags=['evaluation'])
_benchmark_lock = asyncio.Lock()


@router.get('')
def list_runs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [
        {'id': r.id, 'name': r.name, 'metrics': json.loads(r.metrics_json or '{}'), 'created_at': r.created_at.isoformat()}
        for r in db.query(EvaluationRun).order_by(EvaluationRun.id.desc()).limit(20)
    ]


@router.post('/run')
async def run_eval(db: Session = Depends(get_db), user: User = Depends(require_roles('ADMIN'))):
    # One benchmark at a time: it drives the shared simulator and makes a bounded set of LLM calls.
    if _benchmark_lock.locked():
        raise HTTPException(409, 'A benchmark is already running')
    async with _benchmark_lock:
        audit(db, user.id, 'run_benchmark')
        db.commit()
        return await run_async()

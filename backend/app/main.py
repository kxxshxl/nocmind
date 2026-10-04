import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import auth, evaluation, incidents, metrics, rag, simulator, topology
from app.api.routes.auth import ensure_demo_user
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.telemetry import metrics_middleware
from app.db.session import Base, SessionLocal, engine

configure_logging()
log = logging.getLogger('nocmind')


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    if settings.database_url.startswith('postgresql'):
        try:
            with engine.begin() as conn:
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        except Exception as exc:
            log.warning('Could not enable pgvector extension: %s', exc)
    db = SessionLocal()
    try:
        ensure_demo_user(db)
    finally:
        db.close()
    # Load embedding + reranker models in the background: the first RAG query otherwise pays ~6s of model loading.
    threading.Thread(target=_warm_models, daemon=True).start()
    yield


def _warm_models() -> None:
    try:
        from app.rag.embeddings import build_embedder
        from app.rag.reranker import _load_cross_encoder
        build_embedder(settings.embedding_backend, settings.embedding_model, settings.embedding_dim).encode(['warm-up'])
        _load_cross_encoder(settings.reranker_model)
    except Exception as exc:  # pragma: no cover
        log.warning('Model warm-up failed: %s', exc)


app = FastAPI(title='NOCMind API', version='1.0.0', lifespan=lifespan)
app.middleware('http')(metrics_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin, 'http://localhost:5173'],
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type', 'X-Request-ID'],
    expose_headers=['X-Request-ID'],
)
app.include_router(auth.router, prefix='/api/v1')
app.include_router(incidents.router, prefix='/api/v1')
app.include_router(rag.router, prefix='/api/v1')
app.include_router(simulator.router, prefix='/api/v1')
app.include_router(evaluation.router, prefix='/api/v1')
app.include_router(topology.router, prefix='/api/v1')
app.include_router(metrics.router)


@app.get('/health')
def health():
    """Liveness: cheap, no dependencies."""
    return {'status': 'ok', 'service': settings.app_name, 'llm_provider': settings.llm_provider}


@app.get('/health/ready')
async def ready():
    """Readiness: database, cache and LLM provider reachability (short timeouts, no model call)."""
    import httpx

    from app.services.llm import provider_config

    checks: dict[str, object] = {}
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        checks['database'] = 'ok'
    except Exception as exc:
        checks['database'] = f'error: {type(exc).__name__}'
    from app.core.cache import QueryCache
    checks['redis'] = 'ok' if QueryCache()._redis is not None else 'unavailable (in-memory cache fallback)'
    cfg = provider_config(settings.llm_provider)
    llm: dict[str, object] = {'provider': cfg.name, 'model': cfg.model}
    if cfg.kind == 'ollama':
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                tags = (await client.get(settings.ollama_base_url.rstrip('/') + '/api/tags')).json()
            names = {m.get('name') for m in tags.get('models', [])}
            llm['status'] = 'ok' if cfg.model in names or f'{cfg.model}:latest' in names else 'model not pulled'
        except Exception as exc:
            llm['status'] = f'unreachable: {type(exc).__name__}'
    elif cfg.kind == 'openai':
        llm['status'] = 'configured' if cfg.url and cfg.api_key else 'not configured'
    else:
        llm['status'] = 'demo (offline deterministic mode)'
    checks['llm'] = llm
    healthy = checks['database'] == 'ok'
    return JSONResponse({'status': 'ready' if healthy else 'degraded', 'checks': checks}, status_code=200 if healthy else 503)

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

# CPU-only torch keeps the image small; embeddings/reranking are light. The LLM runs in the Ollama container.
COPY requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r requirements.txt

# Bake the embedding and reranker models into the image so startup does not depend on Hugging Face.
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini scripts_seed.py ./

RUN useradd --create-home --uid 10001 nocmind \
 && mkdir -p /data \
 && chown -R nocmind /app /data /opt/hf
USER nocmind

ENV HF_HUB_OFFLINE=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "PYTHONPATH=. alembic upgrade head && PYTHONPATH=. python scripts_seed.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]

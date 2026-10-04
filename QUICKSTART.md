# NOCMind Quickstart

## Docker (recommended)

```bash
cp .env.example .env        # optionally set JWT_SECRET, DEMO_OPERATOR_PASSWORD, DEMO_VIEWER_PASSWORD
docker compose up -d --build                                                  # CPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build  # NVIDIA GPU (recommended)
```

The first start pulls `qwen3:4b-instruct` (about 2.5 GB) into the `ollama` volume. Open http://localhost:5173 and sign in as `admin` / `nocmind-admin`.

## Demo flow

1. **Trigger scenario → Router overload** creates an incident and injects the fault into the simulator.
2. **Investigate with AI**: the LangGraph agent calls telemetry, log, topology and RAG tools, then writes an evidence-backed RCA (about 25–40 s on a laptop GPU).
3. Review the timeline, evidence and proposed remediation, which is marked *awaiting human approval*.
4. **Approve & execute** runs the action, validates telemetry and marks the incident `RESOLVED`.

## Benchmark

UI: **Evaluation → Run benchmark** (ADMIN). CLI:

```bash
make evaluate            # inside the running stack: local Qwen3 agent + local judge
make evaluate-offline    # deterministic demo agent, no LLM, no judge
```

## Local backend without Docker

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # source .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
set PYTHONPATH=. & alembic upgrade head & python scripts_seed.py   # bash: PYTHONPATH=. alembic upgrade head && ...
uvicorn app.main:app --reload --port 8000
```

Defaults to SQLite and `LLM_PROVIDER=demo`. For the local LLM, run Ollama (`ollama pull qwen3:4b-instruct`) and set `LLM_PROVIDER=ollama`.

Frontend: `cd frontend && npm ci && npm run dev`.

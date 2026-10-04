# NOCMind: agentic AI for telecom NOC incident investigation

NOCMind investigates network incidents end to end:

**incident → LangGraph agent → telemetry / log / topology tools → hybrid RAG (runbooks + incident history) → evidence-backed RCA → human approval → remediation → validation → resolution**

By default it runs entirely locally: a **local LLM (Qwen3 4B Instruct via Ollama)** drives tool calling, RCA, remediation
planning and LLM-as-judge evaluation, so investigations and benchmarks need no external API quota. OpenRouter or any
OpenAI-compatible endpoint can be used as the primary or fallback provider through configuration only.

**Docs:** [Full documentation](docs/DOCUMENTATION.md) · [Completion work report](docs/WORK_REPORT.md) ·
[Architecture](docs/architecture.md) · [Evaluation](docs/evaluation.md) · [Security](docs/security.md) · [Verification](docs/verification.md)

## Features

- **LLM-driven LangGraph agent**: triage → plan → tool-calling investigator → RCA → remediation → report. The model picks
  tools and arguments; every RCA and remediation records whether it came from the LLM or from a labelled fallback.
- **Hybrid RAG**: structure-aware chunks, `all-MiniLM-L6-v2` embeddings in pgvector plus PostgreSQL full-text search,
  reciprocal-rank fusion, and a `ms-marco-MiniLM-L-6-v2` cross-encoder reranker. A RAG Explorer UI shows dense, lexical and rerank scores.
- **Safe remediation**: read-only tools only during investigation, per-action target allowlists, explicit
  OPERATOR/ADMIN approval, validation against telemetry, and escalation if validation fails.
- **Graceful degradation**: one attempt per provider. A 429, 5xx or timeout puts the provider in cooldown (no retry storms),
  an optional fallback provider is tried next, and per-stage fallbacks mean an LLM failure never aborts an investigation.
- **Evaluation**: deterministic retrieval, RCA, tool-selection, remediation, citation and latency metrics, kept separate from
  model-judged faithfulness, hallucination and relevance. Results are stored in the DB and MLflow and shown in the UI.
- **Observability**: Prometheus metrics, OpenTelemetry spans for every agent node, tool, RAG query and LLM call,
  token counters, JSON logs with correlation, incident and trace ids, and a provisioned Grafana dashboard.
- **Security**: JWT with an enforced secret strength, VIEWER/OPERATOR/ADMIN roles, login lockout, audit log,
  prompt-injection filtering of retrieved content, and loopback-only infrastructure ports.

## Quick start (Docker Compose)

Requirements: Docker with Compose v2, about 8 GB free RAM and about 10 GB disk. An NVIDIA GPU is optional but recommended.

```bash
cp .env.example .env                       # optional: set JWT_SECRET and demo operator/viewer passwords
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # with NVIDIA GPU
# or, CPU only (much slower LLM): docker compose up -d --build
```

The first start pulls `qwen3:4b-instruct` (about 2.5 GB). Then:

| Service | URL |
|---|---|
| UI | http://localhost:5173 (`admin` / `nocmind-admin`) |
| API docs | http://localhost:8000/docs |
| Readiness | http://localhost:8000/health/ready |
| Grafana | http://localhost:3000 (`admin` / `GRAFANA_ADMIN_PASSWORD`, default `admin`) → *NOCMind Overview* |
| Prometheus | http://localhost:9090 |
| MLflow | http://localhost:5000 (experiment `nocmind`) |

**Demo:** *Trigger scenario → Router overload* → *Investigate with AI* (about 25–40 s on a laptop GPU) → review evidence and RCA →
*Approve & execute* → the incident becomes `RESOLVED` after telemetry validation.

**Benchmark:** *Evaluation → Run benchmark*, or `make evaluate` (local agent and local judge, about 4 minutes on a laptop GPU).

## Configuration (LLM providers)

| Setting | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, `openrouter`/`openai`/`azure` (OpenAI-compatible), or `demo` (offline, deterministic, for tests) |
| `OLLAMA_MODEL` | `qwen3:4b-instruct` | any tool-capable Ollama model |
| `LLM_FALLBACK_PROVIDER` | empty | e.g. `openrouter`, used only when the primary fails |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | | OpenAI-compatible endpoint (full `/chat/completions` URL) |
| `EVAL_JUDGE_PROVIDER` | `ollama` | judge for model-graded metrics, or `none` |
| `LLM_TIMEOUT_SECONDS` / `LLM_COOLDOWN_SECONDS` | 120 / 60 | per-call timeout; skip window after a transient failure |

Switching providers needs only configuration, no code changes. See [.env.example](.env.example).

## Architecture

See [docs/architecture.md](docs/architecture.md). In short: React/Vite → FastAPI (JWT, roles) → `IncidentService` → LangGraph
`StateGraph` → `LLMClient` (Ollama / OpenAI-compatible / demo) plus a JSON-schema `ToolRegistry` (simulator tools, hybrid RAG) →
PostgreSQL + pgvector, Redis cache, Prometheus/OTel, MLflow.

## Evaluation

Methodology: [docs/evaluation.md](docs/evaluation.md). Recorded results: [docs/verification.md](docs/verification.md).
Deterministic metrics (ground truth from the simulator and labelled runbooks) are reported separately from LLM-judged
metrics. Unjudged cases are omitted, never imputed.

## Security

See [docs/security.md](docs/security.md), which covers implemented controls and known gaps (no TLS, single-process login
lockout, unauthenticated `/metrics`).

## Development

```bash
cd backend && pip install -r requirements-dev.txt
make test        # 14 tests: RAG, agent, LLM client, provider failure/timeout/429, tool failure, authz, injection, API flow
make lint        # ruff
make evaluate-offline
cd frontend && npm ci && npm run build
```

Project layout: `backend/app/{agents,ai,api,core,db,evaluation,rag,services,tools}`, `frontend/src`, `infra/{grafana,prometheus,kubernetes}`, `data/runbooks`, `docs/`.

## Limitations

- **Simulated network**: telemetry, logs and remediation effects come from a deterministic simulator with 8 fault scenarios, not real devices.
- **Small benchmark**: 8 scenarios and 8 retrieval queries over a 10-document corpus. Results show the system works end to end and are not statistically robust.
- **Small local model**: Qwen3 4B sometimes calls tools that aren't needed, misdiagnoses ambiguous scenarios (e.g. load-balancer imbalance) and
  picks the wrong remediation. The allowlist and human approval contain this. The default judge is the same small model, so
  model-judged scores are lenient. Use a stronger external judge for more credible scores.
- **CPU-only inference** works but is several times slower than the GPU path.
- **Kubernetes** manifests in `infra/kubernetes` are examples and have **not** been deployed or tested. Nothing has been deployed to a cloud provider.
- Single backend replica: login lockout, the benchmark lock and the simulator are in-process state.

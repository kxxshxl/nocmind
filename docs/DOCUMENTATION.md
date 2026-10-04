# NOCMind documentation

NOCMind is an agentic AI assistant for a telecom Network Operations Center (NOC). When an incident is raised, an LLM agent
investigates it with real tool calls (telemetry, logs, topology, dependency health, runbook and incident-history search),
writes an evidence-backed root-cause analysis (RCA) and proposes one remediation. A human approves the remediation, and
NOCMind executes it, checks that the network recovered and resolves the incident.

Contents
1. [Concepts](#1-concepts)
2. [Architecture](#2-architecture)
3. [Installation](#3-installation)
4. [Configuration reference](#4-configuration-reference)
5. [Using NOCMind (UI guide)](#5-using-nocmind-ui-guide)
6. [Roles and accounts](#6-roles-and-accounts)
7. [Simulator scenarios](#7-simulator-scenarios)
8. [How an investigation works](#8-how-an-investigation-works)
9. [Tools reference](#9-tools-reference)
10. [RAG (retrieval)](#10-rag-retrieval)
11. [REST API reference](#11-rest-api-reference)
12. [Evaluation and benchmark](#12-evaluation-and-benchmark)
13. [Observability](#13-observability)
14. [Security](#14-security)
15. [Operations and troubleshooting](#15-operations-and-troubleshooting)
16. [Development](#16-development)
17. [Extending NOCMind](#17-extending-nocmind)
18. [Deployment notes](#18-deployment-notes)
19. [Limitations](#19-limitations)
20. [Glossary](#20-glossary)

---

## 1. Concepts

| Term | Meaning in NOCMind |
|---|---|
| Incident | A reported problem: title, region, service, severity (SEV-1…SEV-4), symptoms |
| Scenario | A fault injected into the network simulator (e.g. router overload) |
| Investigation | One run of the LangGraph agent over an incident |
| Evidence | Tool results and retrieved documents, each with a `source_id` |
| RCA | Root cause, confidence, alternative causes, cited evidence ids, summary, plus how it was produced (`method`) |
| Remediation | One allowlisted action on one allowlisted target. Always needs human approval |
| Validation | Telemetry checks after remediation. Pass → `RESOLVED`, fail → `ESCALATED` |
| Agent mode | `llm` (model drove the tools), `degraded` (LLM unavailable, planned tools ran directly), `demo` (offline deterministic mode) |

Incident lifecycle:

```
NEW → INVESTIGATING → REMEDIATION_PENDING → REMEDIATION_APPROVED → REMEDIATING → RESOLVED
                   ↘ ESCALATED (agent error)                                   ↘ ESCALATED (validation failed)
```

## 2. Architecture

```
Browser (React/Vite, :5173)
   │  JWT bearer
   ▼
FastAPI (:8000) ── auth/roles · audit · request ids · /metrics · /health
   │
   ▼
IncidentService ──▶ LangGraph StateGraph
                     triage → plan → investigator ⇄ tools → rca → remediation → report
                                        │
              ┌─────────────────────────┼──────────────────────────────┐
              ▼                         ▼                              ▼
       LLMClient                 ToolRegistry                    RAGPipeline
   Ollama (Qwen3 4B) /       simulator telemetry, logs,    MiniLM embeddings · pgvector ·
   OpenAI-compatible /       topology, dependencies,       full-text · RRF · cross-encoder
   demo                      remediation actions           rerank · Redis cache
              │                         │                              │
              └──────────── PostgreSQL 17 + pgvector (incidents, events, tool calls, LLM requests,
                            remediation actions, audit, evaluation runs, documents, vectors)

Observability: Prometheus (:9090) ← /metrics (incl. OpenTelemetry span durations) → Grafana (:3000)
Benchmarks: MLflow (:5000)
```

Code map (`backend/app`):

| Path | Responsibility |
|---|---|
| `main.py` | App factory, lifespan (create tables, pgvector extension, demo users, model warm-up), `/health`, `/health/ready` |
| `api/routes/` | `auth`, `incidents`, `simulator`, `rag`, `topology`, `evaluation`, `metrics` |
| `agents/graph.py` | `InvestigationGraph`: node logic, tool authorization, fallbacks, RCA, remediation, report |
| `agents/langgraph_runner.py` | Builds and runs the LangGraph `StateGraph` |
| `agents/state.py` | `InvestigationState` dataclass |
| `ai/prompts.py` | Versioned system prompts and root-cause categories |
| `services/llm.py` | Provider-neutral LLM client (Ollama, OpenAI-compatible, demo), cooldown and fallback |
| `services/incident_service.py` | Incident lifecycle, approval, execution, validation, metrics, audit |
| `tools/` | Tool registry (JSON schemas, risk levels) and network simulator |
| `rag/` | Chunking, embeddings, pgvector store, hybrid retriever, reranker, pipeline with cache |
| `evaluation/` | Metrics (`evaluator.py`) and the benchmark runner (`run_benchmark.py`) |
| `core/` | Config, security, guardrails, telemetry, logging, cache |
| `db/` | SQLAlchemy models and session. Migrations in `backend/migrations` |

## 3. Installation

### 3.1 Requirements
- Docker Desktop or Docker Engine with Compose v2
- About 8 GB free RAM and about 10 GB disk (images plus a 2.5 GB model)
- Optional: an NVIDIA GPU with the container toolkit. On Windows, Docker Desktop with WSL2 GPU support. Investigations take about 30 s on a laptop RTX 4070; on CPU they are several times slower.

### 3.2 Start the stack

```bash
git clone <repo> nocmind && cd nocmind      # or open the existing folder
cp .env.example .env                        # then edit: JWT_SECRET, demo passwords (optional)

# With NVIDIA GPU (recommended)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
# CPU only
docker compose up -d --build
```

On first start, `ollama-pull` downloads `qwen3:4b-instruct` into the `ollama` volume; later starts skip the download.
The backend runs Alembic migrations and seeds runbooks and historical incidents on every start (idempotent).

Check that it is up:

```bash
docker compose ps                                  # backend/frontend/db/redis/ollama → healthy
curl http://localhost:8000/health/ready            # {"status":"ready", ... "llm": {"status":"ok"}}
```

| Service | URL | Notes |
|---|---|---|
| UI | http://localhost:5173 | |
| API + Swagger | http://localhost:8000/docs | |
| Grafana | http://localhost:3000 | `admin` / `GRAFANA_ADMIN_PASSWORD` |
| Prometheus | http://localhost:9090 | localhost only |
| MLflow | http://localhost:5000 | localhost only |
| Ollama | http://localhost:11434 | localhost only |

### 3.3 Stop and reset

```bash
docker compose down            # stop, keep data
docker compose down -v         # stop and DELETE all data, including the downloaded model
```

### 3.4 Local development without Docker
See §16.

## 4. Configuration reference

All settings are environment variables. Compose reads `./.env`; the backend reads its process environment.

### LLM
| Variable | Default (Compose) | Description |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, `demo`, or any name for an OpenAI-compatible endpoint (`openrouter`, `openai`, `azure`) |
| `LLM_FALLBACK_PROVIDER` | *(empty)* | Tried only when the primary fails. Example: `openrouter` |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama server |
| `OLLAMA_MODEL` | `qwen3:4b-instruct` | Any tool-capable Ollama model. It is pulled automatically at startup |
| `OLLAMA_NUM_CTX` | `16384` | Context window passed to Ollama |
| `OLLAMA_THINK` | `false` | Enable thinking mode for models that support it (slower) |
| `LLM_BASE_URL` | | Full `/chat/completions` URL for OpenAI-compatible providers |
| `LLM_API_KEY` | | API key for that endpoint |
| `LLM_MODEL` | | Model name for that endpoint (e.g. `openrouter/free`) |
| `LLM_API_STYLE` | `openai` | `azure` sends the key as an `api-key` header |
| `LLM_TIMEOUT_SECONDS` | `120` | Per-call timeout |
| `LLM_TEMPERATURE` | `0.1` | |
| `LLM_COOLDOWN_SECONDS` | `60` | How long a provider is skipped after a 429, 5xx, timeout or connection error |
| `EVAL_JUDGE_PROVIDER` | `ollama` | Benchmark judge: `ollama`, `openrouter`, … or `none` |

### Agent and RAG
| Variable | Default | Description |
|---|---|---|
| `MAX_TOOL_ROUNDS` | `5` | Maximum model ↔ tool turns per investigation |
| `MAX_TOOL_RESULT_CHARS` | `4000` | Tool output truncation before it is sent to the model |
| `DRY_RUN` | `true` | Blocks mutating tools unless called through the approval path |
| `EMBEDDING_BACKEND` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | `sentence_transformer` / `all-MiniLM-L6-v2` / `384` | `local_hash` is an offline fallback |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Empty string falls back to lexical reranking |
| `TOP_K` / `RERANK_K` | `12` / `5` | Retrieval candidates and results kept after reranking |
| `CACHE_ENABLED` | `true` | Redis cache for RAG queries (falls back to in-memory) |

### Security and app
| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | `production` refuses to start with a weak JWT secret |
| `JWT_SECRET` | *(empty → ephemeral in dev)* | At least 32 random characters |
| `JWT_EXPIRE_MINUTES` | `480` | |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | `admin` / `nocmind-admin` | Admin account created at startup |
| `DEMO_OPERATOR_PASSWORD` / `DEMO_VIEWER_PASSWORD` | *(empty)* | Creates `operator` / `viewer` accounts when set |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCKOUT_SECONDS` | `5` / `300` | Login lockout |
| `CORS_ORIGIN` | `http://localhost:5173` | |
| `TRACING_ENABLED` | `true` | OpenTelemetry span recording |
| `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD` | `nocmind`, `admin` | Infrastructure credentials |

**Common setups**

```bash
# Local only (default)
LLM_PROVIDER=ollama

# Local, falling back to OpenRouter if Ollama is down
LLM_PROVIDER=ollama
LLM_FALLBACK_PROVIDER=openrouter
LLM_BASE_URL=https://openrouter.ai/api/v1/chat/completions
LLM_API_KEY=sk-or-...
LLM_MODEL=openrouter/free

# Bigger local model (if VRAM allows)
OLLAMA_MODEL=qwen3:8b

# Fully offline, deterministic (CI/tests/demos without a model)
LLM_PROVIDER=demo
```

Apply changes with `docker compose up -d` (Compose recreates only the services whose configuration changed).

## 5. Using NOCMind (UI guide)

1. **Sign in** at http://localhost:5173.
2. **NOC Console**
   - *Trigger scenario* (left panel) injects a fault into the simulator and creates a matching incident.
   - Select an incident to see live telemetry (router CPU, API latency, packet loss) and its symptoms.
   - **Investigate with AI** runs the agent. When it finishes you see:
     - **Agent investigation**: the step timeline (planner, each tool call, guardrail events, RCA, remediation) and an agent-mode badge (*LLM-driven* / *Degraded* / *Offline demo*). A banner lists any LLM errors that were handled.
     - **Incident report**: markdown summary.
     - **Root cause analysis**: root cause, confidence, method (*LLM (model)* or *rule-based fallback*), summary and cited evidence.
     - **Remediation**: action, target, risk and reason.
   - **Approve & execute** records your approval, runs the action, validates telemetry and shows *Remediation validated and incident resolved*. If validation fails, the incident is `ESCALATED`.
3. **RAG Explorer**: run any query and inspect dense, lexical and rerank scores per chunk.
4. **Evaluation**: **Run benchmark** (ADMIN, about 4 minutes on GPU). Each run shows deterministic tables (RCA, agent, citations, latency), a separate model-judged table, a retrieval comparison, a per-scenario breakdown and the raw JSON.

Investigation results are saved, so reopening an incident after a refresh shows the stored RCA and report.

## 6. Roles and accounts

| Capability | VIEWER | OPERATOR | ADMIN |
|---|:-:|:-:|:-:|
| View incidents, timeline, evidence, telemetry, topology, RAG, evaluations | ✓ | ✓ | ✓ |
| Create incidents, trigger/reset simulator, investigate | | ✓ | ✓ |
| Approve and execute remediation | | ✓ | ✓ |
| Run benchmark, read audit log | | | ✓ |

Default accounts: `admin / nocmind-admin` (always), plus `operator` and `viewer` if `DEMO_OPERATOR_PASSWORD` / `DEMO_VIEWER_PASSWORD`
are set (your `.env` sets `nocmind-operator` / `nocmind-viewer`). Accounts are created only if they don't exist yet; changing a password
variable later does not update an existing user.

## 7. Simulator scenarios

Devices: `router-mum-04` (edge router), `router-che-02` (peer), `api-mum-01` (API Gateway), `dns-mum-01`, `db-mum-01`.

| Scenario | Fault signature | Ground-truth root cause | Effective fix |
|---|---|---|---|
| `router-overload` | router CPU 96%, packet loss 18%, throughput 980 Mbps | edge router overload | `redistribute_traffic` → router-mum-04 |
| `congestion` | throughput 1000 Mbps, loss 8% | network congestion | `redistribute_traffic` → router-mum-04 |
| `dns-failure` | DNS latency 1450 ms, resolver unhealthy | DNS resolver failure | `restart_dns` → dns-mum-01 |
| `database-latency` | DB query latency 1250 ms, CPU 94% | database performance degradation | `scale_service` → db-mum-01 |
| `service-crash` | API unhealthy, 100% errors, "process exited" | API service crash | `restart_service` → api-mum-01 |
| `lb-imbalance` | API CPU 95%, "disproportionate traffic" log | load balancer imbalance | `scale_service` → api-mum-01 |
| `auth-degradation` | API errors 25%, auth latency log | authentication dependency degradation | `scale_service` → api-mum-01 |
| `complex` | router saturation and DB latency together | combined network saturation and database degradation | both router and DB fixes are required |

Any other action can still be executed, but it does not change telemetry, so validation fails.

## 8. How an investigation works

1. **Triage**: records severity and symptoms. Incident text is checked for prompt-injection patterns.
2. **Plan**: the LLM proposes read-only tools. Unknown tools are dropped, and a static plan is the fallback.
3. **Investigator**: a tool-calling loop (up to `MAX_TOOL_ROUNDS`). Every tool call is:
   authorized (read-only investigation tools only) → argument-validated (JSON schema) → de-duplicated → executed →
   sanitized for injected instructions → stored (`tool_calls`) → turned into evidence → truncated → returned to the model.
   If the model concludes without searching runbooks or history, it gets one reminder.
4. **RCA**: the LLM returns JSON (`root_cause`, `confidence`, `alternative_causes`, `affected_components`, `evidence_ids`, `summary`).
   Only cited ids that match gathered evidence are attached. If the LLM is unavailable, a transparent rule-based RCA is used and labelled.
5. **Remediation**: the LLM chooses an action and target. Proposals outside the allowlist are replaced by a policy mapping and logged as a `GUARDRAIL` event.
6. **Report**: a markdown report from the LLM, or a template.

**Failure behaviour:** a provider error degrades only the current stage, and the investigation always completes. Transient
errors put the provider into cooldown, so later stages skip it immediately instead of waiting on more timeouts.

## 9. Tools reference

| Tool | Risk | Arguments | Purpose |
|---|---|---|---|
| `get_network_metrics` | READ_ONLY | `device?` | Current telemetry (one or all devices) plus recent history |
| `get_service_health` | READ_ONLY | `service?` | API health and metrics |
| `query_logs` | READ_ONLY | `service?`, `region?` | Logs correlated with the active fault |
| `get_topology` | READ_ONLY | `component?` | Dependency graph |
| `check_dependencies` | READ_ONLY | `service?` | Network, database and DNS dependency status |
| `search_runbooks` | READ_ONLY | `query`, `top_k?` | Hybrid RAG over runbooks |
| `search_historical_incidents` | READ_ONLY | `query`, `region?`, `service?` | Similar past incidents and their resolutions |
| `validate_remediation` | READ_ONLY | `target?` | Post-remediation telemetry checks (used by the service, not offered to the agent) |
| `restart_service` | REVERSIBLE | `target` (api-mum-01) | Restart the API process |
| `restart_dns` | REVERSIBLE | `target` (dns-mum-01) | Restart the resolver |
| `redistribute_traffic` | REVERSIBLE | `target` (router-mum-04) | Shift traffic to peers |
| `scale_service` | HIGH_RISK | `target` (api-mum-01 / db-mum-01) | Add capacity |
| `create_ticket` | HIGH_RISK | `target?` | Escalate to humans |

## 10. RAG (retrieval)

- **Corpus:** 5 runbooks (`RB-ROUTER-001`, `RB-DNS-001`, `RB-DB-001`, `RB-SVC-001`, `RB-CONG-001`) and 5 historical incidents, seeded by `backend/scripts_seed.py` (also exported to `data/runbooks/*.md`).
- **Pipeline:** markdown-heading-aware chunks → `all-MiniLM-L6-v2` embeddings (stored in pgvector and as JSON) →
  dense cosine search plus PostgreSQL `ts_rank_cd` full-text search → reciprocal-rank fusion (k=60) →
  `ms-marco-MiniLM-L-6-v2` cross-encoder rerank → top `RERANK_K`. Results are cached in Redis for 5 minutes.
- **Explore:** RAG Explorer page or `POST /api/v1/rag/query`.

## 11. REST API reference

Base: `http://localhost:8000`. Everything under `/api/v1` needs `Authorization: Bearer <token>` except login.
Interactive docs: `/docs`.

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/health` | public | Liveness |
| GET | `/health/ready` | public | Database, Redis and LLM readiness (503 if the database is down) |
| GET | `/metrics` | public | Prometheus metrics |
| POST | `/api/v1/auth/login` | public | `{username, password}` → `{access_token, role}` |
| GET | `/api/v1/auth/me` | any | Current user |
| GET | `/api/v1/auth/audit?limit=100` | ADMIN | Audit log |
| GET | `/api/v1/incidents` | any | List incidents |
| POST | `/api/v1/incidents` | OPERATOR | Create `{title, region, severity, service, symptoms[]}` |
| GET | `/api/v1/incidents/{id}` | any | Incident with stored RCA, remediation and report |
| POST | `/api/v1/incidents/{id}/investigate` | OPERATOR | Run the agent. Returns status, RCA, remediation, events, tool calls, report, `agent_mode`, `llm_errors` |
| GET | `/api/v1/incidents/{id}/timeline` | any | Stored events |
| GET | `/api/v1/incidents/{id}/evidence` | any | Events plus tool calls with results |
| POST | `/api/v1/incidents/{id}/approve-remediation` | OPERATOR | Approve the proposed action |
| POST | `/api/v1/incidents/{id}/execute-remediation` | OPERATOR | Execute (requires approval) and validate |
| GET | `/api/v1/simulator/state` | any | Device metrics and history |
| POST | `/api/v1/simulator/scenarios/{scenario}` | OPERATOR | Inject a fault |
| POST | `/api/v1/simulator/reset` | OPERATOR | Back to healthy |
| POST | `/api/v1/rag/query` | any | `{query, top_k (1–50), rerank_k (1–20)}` |
| GET | `/api/v1/topology?component=API Gateway` | any | Dependencies |
| GET | `/api/v1/evaluations` | any | Last 20 benchmark runs |
| POST | `/api/v1/evaluations/run` | ADMIN | Run the benchmark (409 if one is already running) |

Send `X-Request-ID` to set your own correlation id; it is echoed back and appears in logs and audit records.

**curl walkthrough**

```bash
API=http://localhost:8000/api/v1
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"nocmind-admin"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
H="Authorization: Bearer $TOKEN"
curl -s -X POST $API/simulator/scenarios/router-overload -H "$H"
INC=$(curl -s -X POST $API/incidents -H "$H" -H 'Content-Type: application/json' \
  -d '{"title":"Latency spike","region":"Mumbai","severity":"SEV-2","service":"API Gateway","symptoms":["high latency","packet loss"]}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -X POST $API/incidents/$INC/investigate -H "$H"
curl -s -X POST $API/incidents/$INC/approve-remediation -H "$H"
curl -s -X POST $API/incidents/$INC/execute-remediation -H "$H"
```

## 12. Evaluation and benchmark

Run it with the UI (**Evaluation → Run benchmark**), `make evaluate`, or:

```bash
docker compose exec backend python -m app.evaluation.run_benchmark --provider ollama --judge ollama
# options: --judge none  ·  --scenarios router-overload,dns-failure
make evaluate-offline        # demo agent, no LLM: quick regression check
```

What it measures (full methodology in [evaluation.md](evaluation.md)):

- **Deterministic:** retrieval precision@5, recall@5, hit rate, MRR and nDCG for lexical, dense, hybrid and hybrid+rerank; RCA top-1/top-3,
  false-diagnosis and abstention rates; share of LLM-generated RCAs; tool selection accuracy, recall and unnecessary-tool rate;
  remediation accuracy; resolution rate; citation correctness and coverage; latency; LLM calls, failures and tokens.
- **Model-judged:** faithfulness, hallucination rate (1 − faithfulness), answer relevance. Failed judgments are counted and left out of averages.

Results are saved in `evaluation_runs` (shown in the UI) and logged to MLflow (experiment `nocmind`).
Latest recorded results: [verification.md](verification.md).

## 13. Observability

**Metrics** (`/metrics`, scraped by Prometheus every 5 s):

| Metric | Labels | Meaning |
|---|---|---|
| `nocmind_requests_total`, `nocmind_request_latency_seconds` | path (route template), method, status | API traffic |
| `nocmind_span_duration_seconds` | span | Duration of `agent.*`, `tool.execute`, `rag.query`, `llm.chat` (from OpenTelemetry) |
| `nocmind_llm_calls_total` | provider, model, status | `success` / `error` / `timeout` / `rate_limited` / `skipped_cooldown` |
| `nocmind_llm_latency_seconds` | provider | Successful call latency |
| `nocmind_llm_tokens_total` | provider, direction | Tokens reported by the provider |
| `nocmind_tool_calls_total` | tool, risk, status | Includes `invalid_args` and `blocked` |
| `nocmind_rag_queries_total`, `nocmind_rag_latency_seconds` | | RAG |
| `nocmind_investigations_total` | outcome, rca_method | Agent mode and RCA provenance |
| `nocmind_remediations_total` | action, status | `validated` / `executed` / `failed` |
| `nocmind_errors_total` | component, kind | Handled failures (llm, tool, agent) |
| `nocmind_active_incidents` | | Not yet resolved or escalated |

**Dashboard:** Grafana → *NOCMind Overview* (the home dashboard) has 15 panels covering KPIs, API rate and latency, agent node
durations, tool/RAG/LLM spans, LLM outcomes, latency and tokens, tool calls, errors and remediations.

**Logs:** JSON on stdout (`docker compose logs -f backend`), with `correlation_id`, `incident_id` and `trace_id` when available.
To follow one investigation: `docker compose logs backend | grep INC-1234567890`.

**Database records:** `llm_requests` (every call: provider, model, prompt version, tokens, latency, status), `tool_calls`,
`incident_events`, `audit_events`.

## 14. Security

Summary (details and known gaps in [security.md](security.md)):
- JWT with an enforced secret strength; PBKDF2 password hashing; login lockout.
- Role-based access (§6). Remediation needs explicit approval, and execution without approval returns 400.
- The agent can only use read-only tools. Remediation actions and targets are allowlisted outside the model.
- Retrieved content and tool output are filtered for prompt injection. Prompts treat them as untrusted data.
- Audit trail for security-relevant actions. Secrets only via env/`.env` (git-ignored).
- Infrastructure ports bind to localhost. There is no TLS, so put a reverse proxy in front for anything beyond your own machine.

## 15. Operations and troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/health/ready` shows `llm.status: model not pulled` | `docker compose up ollama-pull` or `docker compose exec ollama ollama pull qwen3:4b-instruct` |
| `llm.status: unreachable` | Ollama is down: `docker compose up -d ollama`. Investigations still complete in degraded mode |
| Investigations take minutes | CPU inference. Use the GPU override, or check `docker compose exec ollama nvidia-smi` |
| UI keeps returning to login | Token expired or the backend restarted without `JWT_SECRET` (ephemeral secret). Set `JWT_SECRET` in `.env` |
| `429 Too many failed login attempts` | Lockout: wait 5 minutes (`LOGIN_LOCKOUT_SECONDS`) or restart the backend |
| `403 Insufficient role` | Signed in as a viewer: use operator or admin |
| `409 A benchmark is already running` | Wait for the current run to finish |
| Benchmark changed the dashboard telemetry | The benchmark drives the shared simulator and resets it at the end. Avoid live demos while it runs |
| OpenRouter 429 | The provider goes into cooldown automatically. Use Ollama as primary |
| MLflow container restarting | Check `docker stats`; it is limited to 2 GB with 1 worker |
| Port already in use | Change the host port mapping in `docker-compose.yml` |

Useful commands:

```bash
docker compose ps
docker compose logs -f backend
docker compose exec backend python -m app.evaluation.run_benchmark --judge none
docker compose exec db psql -U nocmind -c "select status, count(*) from incidents group by 1"
docker compose exec ollama ollama list
docker compose restart backend
```

Backups: the data lives in the `pgdata` volume. For example: `docker compose exec db pg_dump -U nocmind nocmind > backup.sql`.

## 16. Development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --index-url https://download.pytorch.org/whl/cpu torch
pip install -r requirements-dev.txt
PYTHONPATH=. alembic upgrade head && PYTHONPATH=. python scripts_seed.py
PYTHONPATH=. uvicorn app.main:app --reload --port 8000    # SQLite + LLM_PROVIDER=demo by default
# use the Compose Ollama: LLM_PROVIDER=ollama OLLAMA_BASE_URL=http://localhost:11434

# Frontend
cd frontend && npm ci && npm run dev                      # http://localhost:5173

# Quality gates
make test     # pytest (14 tests, offline, isolated SQLite)
make lint     # ruff
cd frontend && npm run build
```

Tests run offline: `tests/conftest.py` forces `LLM_PROVIDER=demo`, a temporary database and a test JWT secret. LLM behaviour is
tested with scripted clients and simulated HTTP failures (`tests/test_reliability.py`).

`python demo.py` (in `backend/`) runs a full investigate → approve → execute cycle from the CLI.

## 17. Extending NOCMind

- **Add a runbook:** add an entry to `DOCS` in `backend/scripts_seed.py` (id, title, kind, service, severity, markdown body) and restart the
  backend (it reseeds). Optionally add a labelled query to `RETRIEVAL_CASES` in `run_benchmark.py`.
- **Add a tool:** write a handler, register a `ToolSpec` in `tools/default_registry.py` (JSON schema, risk level, `read_only`). Read-only tools
  are offered to the agent automatically. Mutating tools also need entries in the remediation target allowlist in `agents/graph.py`.
- **Add a scenario:** extend `NetworkSimulator.trigger`, `query_logs`, `EFFECTIVE_FIXES` and `validate_remediation` in `tools/simulator_tools.py`.
  Add it to `SCENARIOS` / `EXPECTED_REMEDIATION` in `run_benchmark.py`, the root-cause categories in `ai/prompts.py`, the aliases in
  `evaluation/evaluator.py`, and the frontend scenario list.
- **Change the model:** set `OLLAMA_MODEL` (it is pulled automatically) or point `LLM_PROVIDER` at an OpenAI-compatible endpoint. Then run the benchmark to compare.
- **Schema changes:** add an Alembic migration in `backend/migrations/versions`.

## 18. Deployment notes

- **Tested:** Docker Compose on a single host (Windows 11 + Docker Desktop + NVIDIA GPU).
- **Not tested:** `infra/kubernetes/*.yaml` are reference manifests and have never been applied to a cluster. No cloud deployment exists.
- For a real deployment you would need managed PostgreSQL with pgvector, managed Redis, a GPU node or hosted model endpoint,
  TLS ingress, a secret manager, a strong `JWT_SECRET` with `APP_ENV=production`, Redis-backed rate limiting before running
  multiple backend replicas, and network restrictions on `/metrics`, Prometheus, Grafana and MLflow.

## 19. Limitations

- The network, logs and remediation effects are simulated (8 scenarios), not real devices.
- The benchmark is small and synthetic, so its numbers show the system working, not statistical accuracy.
- Qwen3 4B misdiagnoses some ambiguous faults (`lb-imbalance`, `complex`), calls unnecessary tools (about 29%) and sometimes picks
  the wrong remediation. The allowlist, human approval and validation contain this; it is not eliminated.
- The default judge is the same small model, so judged scores are lenient.
- Prompt-injection filtering is pattern-based and can be bypassed. Safety relies on the hard controls.
- Single backend replica (in-process lockout, benchmark lock and simulator state). No TLS. No OTLP trace backend.

## 20. Glossary

**NOC**: Network Operations Center. **RCA**: root-cause analysis. **RAG**: retrieval-augmented generation. **RRF**:
reciprocal-rank fusion. **MRR / nDCG**: ranking metrics. **LLM-as-judge**: using a model to grade another model's output.
**Cooldown**: the period during which a failed provider is skipped. **Degraded mode**: the investigation continues without the
LLM, using planned tools and rule-based analysis.

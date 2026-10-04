# Architecture

```
React/Vite UI ──JWT──▶ FastAPI ──▶ IncidentService ──▶ LangGraph StateGraph
                                                        triage → plan → investigator → rca → remediation → report
                                                                         │   ▲
                                              read-only tool calls  ◀────┘   │ tool results (sanitised, truncated)
                                                                         ▼   │
                          simulator telemetry · logs · topology · dependencies · hybrid RAG (runbooks, history)
Human approval (OPERATOR/ADMIN) ──▶ remediation tool (allowlisted action+target) ──▶ validation ──▶ RESOLVED / ESCALATED
```

## Components

| Layer | Implementation |
|---|---|
| API | FastAPI, JWT (HS256), role checks, request correlation ids (`X-Request-ID`) |
| Agent | LangGraph `StateGraph` (`app/agents/langgraph_runner.py`) running node methods of `InvestigationGraph` (`app/agents/graph.py`) |
| LLM | `app/services/llm.py`: Ollama native `/api/chat` (default, local), any OpenAI-compatible endpoint (OpenRouter, OpenAI, Azure), or `demo` (offline, deterministic) |
| Tools | `app/tools/default_registry.py`: JSON-schema tools with risk levels; simulator in `app/tools/simulator_tools.py` |
| RAG | Structure-aware chunking → `all-MiniLM-L6-v2` embeddings → pgvector (PostgreSQL) or JSON vectors (SQLite) + PostgreSQL full-text → reciprocal-rank fusion → `ms-marco-MiniLM-L-6-v2` cross-encoder rerank; Redis query cache |
| Storage | PostgreSQL 17 + pgvector (Compose) / SQLite (tests, local dev); Alembic migrations |
| Observability | Prometheus metrics, OpenTelemetry spans exported to a Prometheus histogram, JSON logs with correlation/incident/trace ids, Grafana dashboard, MLflow for benchmark runs |

## Investigation flow

1. **Triage**: records severity and symptoms. Incident text is checked for prompt-injection patterns.
2. **Plan**: the LLM proposes read-only tools (validated against the registry). If it fails, a static plan is used.
3. **Investigator**: a tool-calling loop (max `MAX_TOOL_ROUNDS`). The model chooses tools and arguments. Every call goes through
   authorization (read-only only, never remediation tools), argument-schema validation, de-duplication,
   output sanitisation and truncation. If the model finishes without consulting runbooks or history, it gets a single
   reminder to do so; it still chooses the queries itself.
4. **RCA**: the LLM produces structured JSON (`root_cause`, `confidence`, `evidence_ids`, …) from live evidence plus top-ranked
   documents. Cited ids are checked against the evidence actually gathered.
5. **Remediation**: the LLM chooses one action and target. Anything outside the per-action target allowlist is replaced
   by a policy mapping, and the replacement is logged. Every remediation needs human approval.
6. **Report**: an LLM-written markdown report, or a template if the LLM is unavailable.

Each RCA records how it was produced (`method`: `llm` | `rule_based_fallback`). Each investigation records
`agent_mode` (`llm` | `degraded` | `demo`), so the UI and the benchmark never present fallback output as model output.

## Failure handling

* Each provider gets **one attempt per request**. A 429, 5xx, timeout or connection error puts that provider into a cooldown
  (`LLM_COOLDOWN_SECONDS`, default 60s); during cooldown it is skipped, not retried, which avoids quota hammering.
* An optional `LLM_FALLBACK_PROVIDER` (e.g. Ollama primary, OpenRouter fallback) is tried next.
* A failed stage emits `LLM_ERROR`, increments `nocmind_errors_total` and falls back for that stage only: static plan,
  direct execution of planned tools, rule-based RCA, policy remediation or template report. The investigation never aborts
  because of an LLM failure.
* A failing tool returns `{"success": false, "error": ...}` to the model, and the investigation continues.
* An unexpected agent exception marks the incident `ESCALATED` with an `ERROR` event, so it never stays stuck in `INVESTIGATING`.

## Why a simulator

A deterministic network simulator provides known ground truth, reproducible incidents and measurable evaluation without
private telecom infrastructure. Remediation tools mutate simulator state, and validation reads it back.

## PostgreSQL vector path

With PostgreSQL, ingestion writes vectors to a `document_vectors` pgvector table and the retriever runs cosine-distance search
there, plus `ts_rank_cd` full-text ranking. SQLite uses JSON vectors and in-process cosine similarity.

## Deployment

Docker Compose is the tested deployment (see README). `infra/kubernetes/` contains example manifests that have **not**
been applied to a cluster. They show the intended shape: secrets via `Secret`, readiness on `/health/ready`, and Ollama as an
in-cluster service. A cloud deployment would add managed PostgreSQL with pgvector, managed Redis, a GPU node pool or hosted
model endpoint for the LLM, TLS ingress and a secret manager. None of this has been deployed.

# NOCMind: completion work report

**Period:** 2026-10-03 · **Scope:** finish an existing, partly working NOCMind repository and make it portfolio-ready and production-like on a local machine, without rebuilding it.

This report records the starting state, what changed and why, how each change was verified, the measured results and what is still open. Detailed reference material is in [DOCUMENTATION.md](DOCUMENTATION.md).

---

## 1. Starting state (as found)

### Working
- Docker Compose stack: PostgreSQL + pgvector, Redis, FastAPI backend, React/Vite frontend, Prometheus, Grafana, MLflow.
- JWT login, a network simulator with 8 fault scenarios, and hybrid RAG (MiniLM embeddings, pgvector, full-text search, RRF fusion, cross-encoder reranking).
- A LangGraph investigation (triage → plan → investigator → RCA → remediation → report) with tool calling.
- Approval-gated remediation, validation and resolution.
- An evaluation UI. 3 backend tests passed.
- The LLM provider was OpenRouter (free tier).

### Problems found during inspection

| # | Problem | Impact |
|---|---|---|
| 1 | Benchmarks depended on OpenRouter's free tier, which returned HTTP 429 | Evaluation was not repeatable |
| 2 | An exception from the investigator's LLM call was not caught | One failed LLM request could crash the whole investigation |
| 3 | Failed LLM calls were recorded as `success` and rule-based fallback RCAs were not labelled | Fallback output could be presented as model output, inflating results |
| 4 | Citation correctness compared RCA evidence with itself | The metric was always 1.0 and meant nothing |
| 5 | When the judge failed, heuristic scores were silently mixed into "faithfulness" | Model-judged and heuristic numbers could not be told apart |
| 6 | Retrieved documents were not checked for prompt injection | Only incident text was filtered |
| 7 | The investigator could call any read-only tool. Remediation tools were rejected only incidentally | Tool authorization was implicit |
| 8 | The default JWT secret was `change-me-in-production` | Tokens could be forged |
| 9 | VIEWER role users could trigger the simulator and create or investigate incidents. There was no audit for logins or investigations, no login lockout and no input bounds | Weak role separation |
| 10 | OpenTelemetry API calls existed but no tracer provider was configured | Spans were no-ops |
| 11 | Prometheus path labels used raw URLs (`/incidents/INC-123…`) | Metric cardinality grew without bound |
| 12 | `README.md` contained a copy of the frontend source code | No real README |
| 13 | The backend image was 10.5 GB (CUDA PyTorch, dev tools), with no health check and running as root | Slow, fragile builds |
| 14 | Compose had no restart policies, used `latest` tags, had no resource limits and exposed all ports publicly | Not reproducible, insecure defaults |
| 15 | MLflow was killed by its memory limit after the limit was introduced (see §2.7) | MLflow kept restarting |

---

## 2. Changes by phase

### 2.1 Local LLM (Ollama + Qwen3)

**Decision:** run Ollama as a Compose service with optional NVIDIA GPU support, and default to `qwen3:4b-instruct`. The `qwen3:4b` tag currently resolves to the *thinking* 2507 variant. With tools attached it reasoned out loud (about 1,100 output tokens and 17 s for one tool turn). The instruct variant is far faster in agent loops.

| File | Change |
|---|---|
| `backend/app/services/llm.py` | Rewritten as a provider-neutral client: Ollama native `/api/chat` (tools, `format: json`, `think: false`, `num_ctx`), OpenAI-compatible endpoints, and `demo` mode. Added a `ProviderConfig` per provider and an optional fallback provider. Each provider gets **one attempt per request**: a 429, 5xx, timeout or connection error puts it in a cooldown (`LLM_COOLDOWN_SECONDS`) instead of retrying. Converts OpenAI-style tool messages to Ollama format. Schema hints are now *type descriptions*, because the small model echoed empty example values back. Strips `<think>` blocks. Records provider, model, latency, tokens and status. |
| `backend/app/core/config.py` | New settings: `LLM_FALLBACK_PROVIDER`, `OLLAMA_*`, `LLM_TEMPERATURE`, `LLM_COOLDOWN_SECONDS`, `EVAL_JUDGE_PROVIDER`, `MAX_TOOL_RESULT_CHARS`, login lockout and demo role accounts. |
| `docker-compose.yml`, `docker-compose.gpu.yml` | New `ollama` service and a one-shot `ollama-pull` job that pulls the configured model. A GPU override file enables NVIDIA acceleration. |
| `.env` (yours, git-ignored) | `LLM_PROVIDER=ollama`, `LLM_FALLBACK_PROVIDER=openrouter`, `OLLAMA_MODEL=qwen3:4b-instruct`, `EVAL_JUDGE_PROVIDER=ollama`, demo operator and viewer passwords. Your OpenRouter key is unchanged. |

**Verified:** direct probe of tool calling and JSON mode, then a real end-to-end investigation with the local model. The model chose 5–7 tools on its own and produced the correct RCA and remediation, all labelled `method: llm`.

### 2.2 Agent robustness and tool safety (`backend/app/agents/*`, `app/ai/prompts.py`, `app/core/guardrails.py`)
- **Never crash on an LLM failure:** each stage catches errors, records them (`llm_errors`, an `LLM_ERROR` event, a `status=error` row in `llm_requests`, and `nocmind_errors_total`) and falls back for that stage only. If the investigator gathers no evidence, the planned read-only tools run directly and the run is marked `agent_mode=degraded`.
- **Provenance labels:** RCAs record `method` (`llm` / `rule_based_fallback`) and `model`. Remediations record `method` (`llm` / `policy_mapping`). Investigations record `agent_mode` (`llm` / `degraded` / `demo`).
- **Tool authorization:** the investigator is offered only read-only investigation tools, and any other request is blocked with a `GUARDRAIL` event. Duplicate calls return the cached result. Non-dict arguments are guarded. Tool output is truncated before it goes back to the model.
- **Prompt-injection defence for retrieved content:** a new `sanitize_untrusted()` recursively filters all tool output, including runbooks and history. More patterns were added. Prompts state that tool and retrieved content is untrusted data.
- **RCA grounding:** live evidence is always included, then the top documents. Cited ids are kept separately (`cited_evidence_ids`) and checked against gathered evidence. The RCA prompt lists the known fault categories.
- **Remediation:** the model sees the allowed actions and targets. Proposals outside the allowlist are replaced by the policy mapping and logged. Prompts and tool descriptions now say which device each action affects.
- **Knowledge-base procedure:** if the model finishes without consulting runbooks or history, it gets *one* reminder and still chooses the queries itself. Tool recall went from 0.70 to 1.0.
- **LangGraph runner:** the snapshot is derived from the dataclass fields (no hand-maintained copy) and each node runs inside an `agent.<node>` span.
- **Simulator:** `get_network_metrics` with an unknown device now returns all devices plus a note instead of `{}`.

### 2.3 Evaluation (`backend/app/evaluation/*`)
- One pass: 8 retrieval queries plus 8 scenarios, each investigated once and judged once (about 63 LLM calls).
- **Deterministic vs model-judged** are separate sections. A judge failure is counted and **left out**; heuristic scores are never substituted.
- Fixed **citation correctness** (cited ids vs gathered evidence) and added citation coverage.
- New metrics: `llm_generated_rca_rate`, `llm_rca_top1_accuracy`, `llm_driven_rate`, `remediation_accuracy`, `resolution_rate` (proposed action executed with simulated approval, then validated), LLM call count and failures, token totals, and p50/p95 latency by nearest rank.
- The benchmark never uses cross-provider fallback (agent or judge), so results always come from known models. CLI flags: `--provider`, `--judge`, `--scenarios`.
- `per_scenario` detail, `run_config` and a `methodology` block are stored with every run and logged to MLflow (`mlflow-skinny`).
- **Simulator correctness fix found by the benchmark:** wrong remediations used to "heal" the API, so the misdiagnosed load-balancer case still counted as resolved. Remediation is now fault-aware (`EFFECTIVE_FIXES`): only correct (action, target) pairs change telemetry, and the `complex` scenario needs both a router fix and a database fix.
- `scripts/verify.py` was rewritten to reuse the benchmark scenarios.

### 2.4 Observability (`backend/app/core/telemetry.py`, `logging.py`, `infra/grafana/*`)
- OpenTelemetry `TracerProvider` with a processor that exports **span durations to Prometheus** (`nocmind_span_duration_seconds{span}`), so no extra collector is needed. Spans cover the investigation, each agent node, `tool.execute`, `rag.query` and `llm.chat`.
- New metrics: `nocmind_investigations_total{outcome,rca_method}`, `nocmind_errors_total{component,kind}`, LLM calls by outcome (`success` / `error` / `timeout` / `rate_limited` / `skipped_cooldown`), and tokens by provider. `nocmind_active_incidents` and `nocmind_remediations_total` are now actually updated.
- Request path labels use route templates, which bounds cardinality.
- **Correlation:** `X-Request-ID` is accepted or generated and returned. JSON logs carry `correlation_id`, `incident_id` and `trace_id`.
- Grafana dashboard rebuilt with 15 panels; the datasource has a fixed uid and the dashboard is set as Grafana's home page.

### 2.5 Security (`backend/app/core/security.py`, `app/api/routes/*`, `app/schemas/models.py`, `app/main.py`)
- JWT secret must be at least 32 characters. Development falls back to an ephemeral random secret; production refuses to start.
- Roles enforced: VIEWER is read-only; OPERATOR can create, investigate, run the simulator, approve and execute; ADMIN can also run the benchmark and read the audit log (`GET /api/v1/auth/audit`). Demo `operator` and `viewer` accounts are created only if their passwords are configured.
- Login lockout: 5 failures in 5 minutes gives HTTP 429.
- Audit events for login and failed login, incident creation, investigation, simulator trigger and reset, approval, execution (with the validation result) and benchmark runs, each with a correlation id.
- Pydantic bounds on incident and RAG inputs (lengths, severity pattern, `top_k` limits). CORS restricted to the methods and headers actually used.
- The benchmark endpoint is async with a single-run lock (409 if one is already running).
- An unexpected agent exception escalates the incident (no stuck `INVESTIGATING` state) and returns a clean 500.

### 2.6 Performance and reliability
- Measured first, then optimised: a cold process had RAG p95 of 6.5 s because models loaded on the first request. Models now preload in a background thread at startup, giving a cold p95 of **688 ms**.
- New `GET /health/ready` checks the database, Redis and Ollama reachability, and whether the configured model has been pulled.

### 2.7 Docker and deployment
- **Backend image:** CPU-only PyTorch (the LLM runs in Ollama), embedding and reranker models baked in (`HF_HUB_OFFLINE=1`), non-root user, health check, `.dockerignore`, dev tools moved to `requirements-dev.txt`. Size went from **10.5 GB to 3.0 GB**.
- **Frontend image:** `npm ci` from the lockfile, `nginx:1.27-alpine`, health check.
- **Compose:** pinned images (Ollama 0.35.1, Prometheus v3.15.0, Grafana 13.2.3, MLflow v3.16.1), `restart: unless-stopped`, health-gated startup order, memory limits, and loopback-only ports for infrastructure. MLflow now uses a persistent volume and a single worker, which fixed its out-of-memory restart loop. Backend has `MLFLOW_TRACKING_URI` with no retries.
- **Kubernetes** examples updated (production env, `/health/ready` probe, realistic memory, Ollama URL, no secret value in the example). Not deployed.
- **CI:** CPU PyTorch, dev requirements, ruff, `npm ci`, and `docker compose config` validation.

### 2.8 Tests, lint and docs
- Tests went from **3 to 14**. New files: `tests/conftest.py` (isolated offline environment), `tests/test_reliability.py` (provider failure, timeout and cooldown, 429 fallback, tool exception, unauthorized tool, prompt injection in RAG, Ollama message conversion) and `tests/test_api.py` (health and metrics, auth required, full role and approval flow with audit, validation, lockout). Updated `test_llm_client.py`.
- Ruff rules pinned in `pyproject.toml`; lint is clean.
- Docs: new `README.md` and `QUICKSTART.md`; rewritten `docs/architecture.md`, `evaluation.md`, `security.md` and `verification.md`; new `.env.example` files; this report; `DOCUMENTATION.md`.
- Frontend: automatic logout on 401, readable API error messages, agent-mode badge and LLM-error banner, RCA method and model shown, and a structured evaluation view (metric tables, retrieval table, per-scenario table, raw JSON in a collapsible section).

---

## 3. Verification summary

| Check | Result |
|---|---|
| `pytest` | 14 passed |
| `ruff check app tests scripts` | clean |
| `npm run build` | succeeds (bundle-size warning only) |
| Clean `docker compose down` then `up --build` (GPU) | all services up; health checks pass |
| Live end-to-end on Docker (router overload) | LLM-driven, 7 tools including RAG, RCA 0.98 (`llm`), execute before approval refused (400), approve, execute, validated, **RESOLVED** |
| Live outage (Ollama stopped, fallback off) | completed in 7.7 s, `degraded`, rule-based RCA correct, remediation pending approval |
| Load: 200 × health, 100 × list, 40 × RAG | all 200 OK; RAG p95 688 ms (cold-start fix) |
| Observability | span, LLM, token and investigation metrics present; correlated logs; Prometheus target up; Grafana dashboard provisioned; MLflow runs logged |

### Benchmark (local agent + local judge, in Docker, no external API)

| Metric | Value |
|---|---|
| RCA top-1 / top-3 | 0.75 / 0.875 (100% LLM-generated) |
| Tool recall / selection accuracy / unnecessary-tool rate | 1.0 / 0.714 / 0.286 |
| Remediation accuracy / resolution rate | 0.714 / 0.625 |
| Citation correctness / coverage | 1.0 / 1.0 |
| Hybrid retrieval hit rate / MRR / nDCG@5 | 1.0 / 1.0 / 0.961 |
| Model-judged faithfulness / hallucination / relevance | 0.875 / 0.125 / 0.875 |
| Investigation p50 / p95 | 28.7 s / 32.2 s |
| LLM calls / failures / tokens in/out | 63 / 0 / 76.8k / 15.4k |

The lower resolution rate compared with the first run is a *correction*: it comes from the simulator fix in §2.3, not a regression.

---

## 4. Decisions and trade-offs
- **No retries on external APIs**, by design: a cooldown plus an optional fallback, as you asked.
- **Same small model as judge**: chosen for quota independence, and labelled as a weak signal. An external judge can be selected with `EVAL_JUDGE_PROVIDER`.
- **Knowledge-source reminder**: a procedural nudge, not hard-coded tool calls. The model still chooses whether and how to query.
- **Spans exported to Prometheus instead of a trace backend**: gives useful latency data with no new dependencies. A real OTLP collector would be the next step.
- **Kubernetes and cloud not deployed**: documented as architecture only. Nothing is claimed as deployed.

## 5. Known limitations and suggested next steps
1. Small synthetic benchmark (8 scenarios, a 10-document corpus). Next: more scenarios and a larger runbook corpus.
2. The 4B model misdiagnoses `lb-imbalance` and `complex` and calls unnecessary tools. Next: try `qwen3:8b`, add few-shot examples, or allow multi-action remediation plans for cascading faults.
3. The judge is the same model. Next: an external or larger local judge plus a small human-labelled set.
4. No TLS, single-process lockout and locks, unauthenticated `/metrics`. Next: a reverse proxy with TLS, Redis-backed rate limiting, and network-restricted metrics.
5. No OTLP trace export. Next: an `opentelemetry-exporter-otlp` collector with Tempo or Jaeger.
6. The project folder is not a git repository. Next: `git init` and a first commit (`.gitignore` already excludes `.env`).

## 6. Files touched
**Backend:** `app/services/llm.py`, `app/core/{config,telemetry,logging,security,guardrails}.py`, `app/agents/{graph,state,langgraph_runner}.py`, `app/ai/prompts.py`, `app/api/routes/{auth,incidents,simulator,evaluation}.py`, `app/schemas/models.py`, `app/main.py`, `app/services/incident_service.py`, `app/tools/{simulator_tools,default_registry}.py`, `app/evaluation/{evaluator,run_benchmark}.py`, `app/rag/{embeddings,retriever}.py`, `scripts/verify.py`, `tests/*`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `Dockerfile`, `.dockerignore`, `.env.example`.
**Frontend:** `src/main.tsx`, `src/styles.css`, `Dockerfile`, `.dockerignore`.
**Infra and root:** `docker-compose.yml`, `docker-compose.gpu.yml`, `infra/grafana/*`, `infra/kubernetes/{backend,secrets.example}.yaml`, `.github/workflows/ci.yml`, `Makefile`, `.gitignore`, `.env.example`, `README.md`, `QUICKSTART.md`, `docs/*`.

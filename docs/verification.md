# Verification record (2026-10-03)

Environment: Windows 11, Docker Desktop (WSL2), RTX 4070 Laptop GPU (8 GB), stack started with
`docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`. Agent and judge: `qwen3:4b-instruct` via Ollama 0.35.1.

## Automated checks
- `pytest`: **14 passed**. Covers the RAG pipeline; the investigation graph; OpenAI-compatible tool-call parsing; Ollama message
  conversion; provider failure → degraded but complete investigation; timeout → cooldown with no retry; 429 → fallback provider;
  tool exceptions and unauthorized tool requests contained; prompt injection in retrieved runbooks neutralised; and the API flow
  (auth, roles, approval gate, input validation, login lockout, audit trail).
- `ruff check app tests scripts`: clean. `npm run build`: succeeds (one bundle-size warning).
- `docker compose config -q`: valid. A clean `down` then `up --build` brought all services up; backend, frontend, db, redis and ollama report healthy.

## Live end-to-end (Docker stack, local LLM)
Router overload via the REST API: login → trigger scenario → create incident → investigate (~32 s) → RCA
`edge router overload` (confidence 0.98, `method: llm`, cited evidence: live telemetry, logs, dependencies, `HIST-1001`,
`RB-ROUTER-001` chunks) → `execute-remediation` before approval refused (**400**) → approve → execute `redistribute_traffic`
on `router-mum-04` → validation checks all true → **RESOLVED**. The agent called 7 read-only tools, including both RAG tools.

## Live failure tests
- **Provider outage** (Ollama container stopped, fallback disabled): investigation completed in 7.7 s, `agent_mode: degraded`,
  4 LLM stage errors recorded, planned tools executed directly, rule-based RCA `DNS resolver failure` (labelled
  `rule_based_fallback`), remediation `restart_dns` from policy mapping, still waiting for approval.
- **Repeated requests**: 200 × `/health` at concurrency 50, p95 95 ms. 100 × incident list, p95 43 ms. 40 distinct RAG queries at
  concurrency 10, p95 688 ms (after adding startup model warm-up; it was 6.5 s on a cold process before). Cached RAG p50 64 ms. All 200 OK.

## Observability
`/metrics` exposes request, LLM-call (by outcome), token, tool, RAG, investigation, remediation, error and per-span duration
series. Spans recorded: `agent.investigation`, `agent.{triage,plan,investigator,rca,remediation,report}`, `tool.execute`,
`rag.query`, `llm.chat`. Logs carry `correlation_id` (`X-Request-ID`), `incident_id` and `trace_id`. The Prometheus target
is up, the Grafana *NOCMind Overview* dashboard is provisioned, and benchmark runs are logged to MLflow.

## Benchmark (local agent + local judge, run inside the backend container, no external API)
`docker compose exec backend python -m app.evaluation.run_benchmark --provider ollama --judge ollama`:
63 LLM calls, 0 failed, 76.8k input / 15.4k output tokens, 256 s wall clock.

Deterministic:

| Metric | Value |
|---|---|
| Retrieval hybrid: hit rate / MRR / nDCG@5 | 1.0 / 1.0 / 0.961 |
| Retrieval hybrid + rerank: hit rate / MRR / nDCG@5 | 1.0 / 1.0 / 0.907 |
| Retrieval lexical / dense nDCG@5 | 0.939 / 0.911 |
| RCA top-1 / top-3 accuracy (8 scenarios, 100% LLM-generated) | 0.75 / 0.875 |
| False-diagnosis rate | 0.25 (`complex` and `lb-imbalance` diagnosed as edge router overload) |
| Tool recall / tool selection accuracy / unnecessary-tool rate | 1.0 / 0.714 / 0.286 |
| Remediation accuracy (7 scenarios with a single correct fix) | 0.714 |
| Resolution rate (proposed action executed + validated) | 0.625 |
| Citation correctness / coverage | 1.0 / 1.0 |
| Investigation latency p50 / p95 | 28.7 s / 32.2 s |
| Mean LLM call latency | 3.6 s |

Model-judged (Qwen3 4B judging Qwen3 4B, 8/8 cases judged): faithfulness 0.875, hallucination rate 0.125, answer relevance 0.875.
Treat these as a weak signal: the judge is the same small model.

During development, the first benchmark exposed a simulator flaw: wrong remediations still "healed" the API. Simulator
remediation is now fault-aware, so only effective (action, target) pairs change telemetry. The numbers above come after that fix.
The `complex` scenario needs two actions and cannot be resolved by a single proposal.

## Not verified
- Kubernetes manifests (not applied to any cluster) and any cloud deployment.
- CPU-only LLM performance (only the GPU path was measured).
- An external judge (OpenRouter) was not used in these runs.

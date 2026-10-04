# Security model

These controls are implemented and covered by tests (`backend/tests/test_api.py`, `test_reliability.py`). This is a
portfolio/demo system; it has not had an external security review.

## Authentication and secrets
- JWT (HS256) bearer tokens with expiry (`JWT_EXPIRE_MINUTES`). Passwords use PBKDF2-SHA256 with 210k iterations and a per-user salt.
- `JWT_SECRET` must be at least 32 characters. If it is missing or a known default, development mode generates an
  **ephemeral random secret** (tokens stop working on restart) and `APP_ENV=production` **refuses to start**.
- Login lockout: 5 failures within 5 minutes per username returns 429. This is in-process, so it does not cover multiple replicas.
- Secrets come only from environment variables (`.env`, git-ignored). `.env.example` contains no real values. Provider keys
  are never logged or returned by the API; `/health/ready` reports only whether a provider is configured.
- Postgres, Redis, Ollama, Prometheus, Grafana and MLflow ports bind to `127.0.0.1` in Compose. Only the UI (5173) and API (8000) are exposed.

## Authorization (roles)
| Action | VIEWER | OPERATOR | ADMIN |
|---|---|---|---|
| Read incidents, timeline, evidence, telemetry, RAG, evaluations | ✓ | ✓ | ✓ |
| Create incident, trigger/reset simulator, run investigation | | ✓ | ✓ |
| Approve / execute remediation | | ✓ | ✓ |
| Run benchmark, read audit log (`GET /api/v1/auth/audit`) | | | ✓ |

The demo `operator` and `viewer` accounts are created only when `DEMO_OPERATOR_PASSWORD` / `DEMO_VIEWER_PASSWORD` are set.

## Agent and tool controls (enforced outside the model)
- The investigator is offered **read-only tools only**. Requests for any other tool are blocked and logged as `GUARDRAIL` events.
- Tool arguments are validated against each tool's JSON schema. Unknown or missing arguments are rejected.
- Remediation is limited to an allowlist of actions, each with its own allowed targets. Model proposals outside the allowlist are replaced and logged.
- Execution requires an explicit `APPROVED` record from an OPERATOR/ADMIN. `execute-remediation` without approval returns 400.
  Mutating tools are also blocked by `DRY_RUN=true` unless called through the approval path.
- After execution, recovery is validated against telemetry. A failed validation escalates the incident rather than resolving it.

## Prompt-injection resistance (defence in depth, not a guarantee)
- Incident text and **all tool output, including retrieved runbooks and historical incidents**, pass through a pattern filter
  (`app/core/guardrails.py`). Instruction-like text is replaced with `[BLOCKED_INSTRUCTION]` and a `GUARDRAIL` event is emitted.
- System prompts state that tool and retrieved content is untrusted data.
- Pattern filters can be bypassed. The real protection is that a manipulated model **still cannot** call mutating tools,
  pick targets outside the allowlist or skip human approval.

## Audit
Audit records include actor, incident, correlation id and details for: login and failed login, incident creation,
investigation, simulator trigger/reset, remediation approval and execution (with the validation outcome), and benchmark runs.
Every tool call, including blocked ones, is stored in `tool_calls`, and every LLM request in `llm_requests` with its status and tokens.

## Known gaps
- No TLS termination. Put a reverse proxy or ingress in front for anything beyond localhost.
- `/metrics` is unauthenticated, which is typical for Prometheus scraping. Restrict it at the network level.
- No refresh tokens or token revocation list. Lockout state is per process.
- Demo passwords are defaults. Change them in `.env`.

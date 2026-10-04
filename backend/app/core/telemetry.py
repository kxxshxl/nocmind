from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.core.config import settings

REQUESTS = Counter("nocmind_requests_total", "HTTP requests", ["path", "method", "status"])
LATENCY = Histogram("nocmind_request_latency_seconds", "HTTP request latency", ["path"])
LLM_CALLS = Counter("nocmind_llm_calls_total", "LLM calls by outcome", ["provider", "model", "status"])
LLM_TOKENS = Counter("nocmind_llm_tokens_total", "LLM tokens reported by the provider", ["provider", "direction"])
LLM_LATENCY = Histogram("nocmind_llm_latency_seconds", "Successful LLM call latency", ["provider"],
                        buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120))
TOOL_CALLS = Counter("nocmind_tool_calls_total", "Tool calls", ["tool", "risk", "status"])
RAG_QUERIES = Counter("nocmind_rag_queries_total", "RAG queries")
RAG_LATENCY = Histogram("nocmind_rag_latency_seconds", "RAG latency")
ACTIVE_INCIDENTS = Gauge("nocmind_active_incidents", "Incidents not yet resolved or escalated")
REMEDIATIONS = Counter("nocmind_remediations_total", "Remediation executions", ["action", "status"])
INVESTIGATIONS = Counter("nocmind_investigations_total", "Investigations by outcome", ["outcome", "rca_method"])
SPAN_LATENCY = Histogram("nocmind_span_duration_seconds", "Duration of traced operations (agent nodes, tools, RAG, LLM)",
                         ["span"], buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300))
ERRORS = Counter("nocmind_errors_total", "Handled failures by component", ["component", "kind"])

log = logging.getLogger("nocmind.telemetry")

# Correlation id for the current request / investigation; included in every JSON log line.
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
incident_id_var: ContextVar[str] = ContextVar("incident_id", default="")

_tracer = None
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanProcessor

    class _PrometheusSpanProcessor(SpanProcessor):
        """Exports OpenTelemetry span durations to Prometheus so traces need no extra collector locally."""

        def on_end(self, span) -> None:  # type: ignore[override]
            if span.start_time and span.end_time:
                SPAN_LATENCY.labels(span.name).observe((span.end_time - span.start_time) / 1e9)

    if settings.tracing_enabled:
        provider = TracerProvider()
        provider.add_span_processor(_PrometheusSpanProcessor())
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("nocmind")
except Exception:  # pragma: no cover
    _tracer = None


def current_trace_id() -> str:
    if _tracer is None:
        return ""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else ""


@contextmanager
def span(name: str, attributes: dict | None = None):
    if _tracer is None:
        started = time.perf_counter()
        try:
            yield None
        finally:
            SPAN_LATENCY.labels(name).observe(time.perf_counter() - started)
        return
    attrs = dict(attributes or {})
    if correlation_id.get():
        attrs.setdefault("correlation.id", correlation_id.get())
    if incident_id_var.get():
        attrs.setdefault("incident.id", incident_id_var.get())
    with _tracer.start_as_current_span(name, attributes=attrs) as current:
        yield current


async def metrics_middleware(request, call_next):
    cid = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = correlation_id.set(cid[:64])
    start = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = correlation_id.get()
        return response
    finally:
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        # Use the route template (/incidents/{incident_id}) to keep metric cardinality bounded.
        path = getattr(route, "path", None) or ("unmatched" if response is not None and response.status_code == 404 else request.url.path)
        status = str(response.status_code) if response is not None else "500"
        REQUESTS.labels(path=path, method=request.method, status=status).inc()
        LATENCY.labels(path=path).observe(elapsed)
        correlation_id.reset(token)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import HistoricalIncident
from app.rag.pipeline import RAGPipeline
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.simulator_tools import (
    check_dependencies,
    create_ticket,
    get_network_metrics,
    get_service_health,
    get_topology,
    query_logs,
    redistribute_traffic,
    restart_dns,
    restart_service,
    scale_service,
    validate_remediation,
)


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def build_registry(db: Session | None = None) -> ToolRegistry:
    r = ToolRegistry()
    defs = [
        ToolSpec("get_network_metrics", "Read current network telemetry (CPU, latency, packet loss, throughput, DNS and DB latency). Omit device to get all devices. Devices: router-mum-04, router-che-02, api-mum-01, dns-mum-01, db-mum-01.", "READ_ONLY", True, False,
                 _schema({"device": {"type": "string"}}, []), get_network_metrics),
        ToolSpec("get_service_health", "Read current health and performance for a service.", "READ_ONLY", True, False,
                 _schema({"service": {"type": "string"}}, []), get_service_health),
        ToolSpec("query_logs", "Read service logs correlated to the active simulator incident.", "READ_ONLY", True, False,
                 _schema({"service": {"type": "string"}, "region": {"type": "string"}}, []), query_logs),
        ToolSpec("get_topology", "Read service dependency topology.", "READ_ONLY", True, False,
                 _schema({"component": {"type": "string"}}, []), get_topology),
        ToolSpec("check_dependencies", "Read dependency health for a service.", "READ_ONLY", True, False,
                 _schema({"service": {"type": "string"}}, []), check_dependencies),
        ToolSpec("search_runbooks", "Search runbooks and troubleshooting documents with hybrid RAG.", "READ_ONLY", True, False,
                 _schema({"query": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
                 lambda query, top_k=5: _rag_search(db, query, top_k=top_k, kind="runbook")),
        ToolSpec("search_historical_incidents", "Search historical incidents for similar symptoms and resolutions.", "READ_ONLY", True, False,
                 _schema({"query": {"type": "string"}, "region": {"type": "string"}, "service": {"type": "string"}}, ["query"]),
                 lambda query, region="", service="": _historical_search(db, query, region, service)),
        ToolSpec("restart_service", "Restart the crashed API application process (target api-mum-01). Only helps when the API process itself has failed.", "REVERSIBLE", False, True,
                 _schema({"target": {"type": "string"}}, ["target"]), restart_service),
        ToolSpec("restart_dns", "Restart the unhealthy DNS resolver (target dns-mum-01).", "REVERSIBLE", False, True,
                 _schema({"target": {"type": "string"}}, ["target"]), restart_dns),
        ToolSpec("redistribute_traffic", "Shift traffic away from the saturated edge router to peers (target router-mum-04). Helps router overload and congestion.", "REVERSIBLE", False, True,
                 _schema({"target": {"type": "string"}}, ["target"]), redistribute_traffic),
        ToolSpec("scale_service", "Add capacity: database (target db-mum-01) for database saturation, or API backends (target api-mum-01) for overloaded or degraded backends. High risk.", "HIGH_RISK", False, True,
                 _schema({"target": {"type": "string"}}, ["target"]), scale_service),
        ToolSpec("create_ticket", "Create an escalation ticket when evidence is insufficient or risk is high.", "HIGH_RISK", False, False,
                 _schema({"target": {"type": "string"}}, []), create_ticket),
        ToolSpec("validate_remediation", "Read current telemetry and validate whether the service is healthy after a remediation.", "READ_ONLY", True, False,
                 _schema({"target": {"type": "string"}}, []), validate_remediation),
    ]
    for spec in defs:
        r.register(spec)
    return r


def _rag_search(db: Session | None, query: str, top_k: int = 5, kind: str = "") -> dict:
    if db is None:
        return {"results": [], "message": "RAG database unavailable"}
    rows = RAGPipeline(db).query(query, top_k=min(top_k * 8, 50), rerank_k=min(top_k * 4, 20))
    if kind:
        rows = [r for r in rows if r.metadata.get("kind") == kind]
    return {"results": [
        {"chunk_id": r.chunk_id, "document_id": r.document_id, "title": r.metadata.get("title", r.document_id),
         "section": r.section, "text": r.text, "score": round(float(r.rerank_score or r.score), 4), "metadata": r.metadata}
        for r in rows[:top_k]
    ]}


def _historical_search(db: Session | None, query: str, region: str = "", service: str = "") -> dict:
    if db is None:
        return {"results": []}
    tokens = {x for x in query.lower().split() if len(x) > 2}
    candidates = db.query(HistoricalIncident).all()
    scored = []
    for item in candidates:
        text = " ".join([item.region, item.service, item.root_cause, item.resolution, *json.loads(item.symptoms_json or "[]")]).lower()
        score = sum(t in text for t in tokens)
        if region and item.region.lower() == region.lower():
            score += 2
        if service and item.service.lower() == service.lower():
            score += 2
        scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return {"results": [
        {"incident_id": i.id, "region": i.region, "service": i.service, "severity": i.severity,
         "symptoms": json.loads(i.symptoms_json or "[]"), "root_cause": i.root_cause, "resolution": i.resolution, "score": s}
        for s, i in scored[:5] if s > 0
    ]}


# Compatibility for older imports.
registry = build_registry()

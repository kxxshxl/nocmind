from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.state import InvestigationState
from app.ai.prompts import INVESTIGATOR_V1, PLANNER_V1, RCA_V1, REMEDIATION_V1, REPORT_V1
from app.core.config import settings
from app.core.guardrails import sanitize_untrusted, sanitize_user_text
from app.core.telemetry import ERRORS, incident_id_var, span
from app.db.models import Incident, LLMRequest, ToolCall
from app.services.llm import LLMClient
from app.services.llm import ToolCall as LLMToolCall
from app.tools.default_registry import build_registry

log = logging.getLogger("nocmind.agent")

ROOT_LABELS = {
    "router-overload": "edge router overload",
    "dns-failure": "DNS resolver failure",
    "database-latency": "database performance degradation",
    "service-crash": "API service crash",
    "congestion": "network congestion",
    "lb-imbalance": "load balancer imbalance",
    "auth-degradation": "authentication dependency degradation",
    "complex": "combined network saturation and database degradation",
}

LIVE_SOURCE_TYPES = {"telemetry", "logs", "topology"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class InvestigationGraph:
    def __init__(self, db: Session, llm: LLMClient | None = None):
        self.db = db
        self.llm = llm or LLMClient()
        self.registry = build_registry(db)

    async def run(self, incident_id: str) -> InvestigationState:
        inc = self.db.get(Incident, incident_id)
        if not inc:
            raise ValueError("Incident not found")
        symptoms = json.loads(inc.symptoms_json or "[]")
        state = InvestigationState(
            incident_id=incident_id,
            incident={
                "title": inc.title,
                "region": inc.region,
                "severity": inc.severity,
                "service": inc.service,
                "symptoms": symptoms,
            },
            agent_mode="demo" if self.llm.is_demo else "llm",
        )
        token = incident_id_var.set(incident_id)
        try:
            with span("agent.investigation", {"incident.id": incident_id, "llm.provider": self.llm.provider}):
                try:
                    from app.agents.langgraph_runner import build_and_run
                except ImportError:
                    return await self.run_linear(state)
                return await build_and_run(self, state)
        finally:
            incident_id_var.reset(token)

    async def run_linear(self, state: InvestigationState) -> InvestigationState:
        for node in (self.triage, self.plan, self.investigator, self.rca, self.remediation, self.report):
            if state.step_count >= settings.max_agent_steps * 4:
                state.emit("GUARDRAIL", "Maximum investigation steps reached")
                break
            await node(state)
        return state

    # ------------------------------------------------------------------ nodes

    async def triage(self, s: InvestigationState):
        s.emit(
            "TRIAGE",
            f"Triage completed for {s.incident['service']} in {s.incident['region']}",
            severity=s.incident["severity"],
            symptoms=s.incident["symptoms"],
        )

    async def plan(self, s: InvestigationState):
        incident_copy = dict(s.incident)
        raw_symptoms = [str(x) for x in incident_copy.get("symptoms", [])]
        cleaned, flagged = sanitize_user_text(" ".join(raw_symptoms))
        if flagged:
            incident_copy["symptoms"] = [cleaned]
            s.emit("GUARDRAIL", "Prompt-injection pattern detected in incident text and sanitized before model use")
        fallback = self._fallback_plan(s)
        if self.llm.is_demo:
            s.plan = fallback
        else:
            try:
                data = await self.llm.generate_json(
                    PLANNER_V1,
                    json.dumps(incident_copy, separators=(",", ":")),
                    {"tools": [{"name": "get_network_metrics", "args": {}}], "reason": ""},
                )
                self._record_llm(s, "planner_v1")
                requested = data.get("tools", []) if isinstance(data, dict) else []
                allowed = {t.name for t in self.registry.list(read_only_only=True)}
                plan = [
                    {"name": item["name"], "args": item.get("args") if isinstance(item.get("args"), dict) else {}}
                    for item in requested if isinstance(item, dict) and item.get("name") in allowed
                ]
                s.plan = plan or fallback
            except Exception as exc:
                self._llm_failed(s, "planner", exc)
                s.plan = fallback
        s.emit("PLANNER", "Investigation plan selected", tools=[x["name"] for x in s.plan])

    def _fallback_plan(self, s: InvestigationState) -> list[dict[str, Any]]:
        symptoms = " ".join(s.incident["symptoms"]).lower()
        plan = [
            {"name": "get_network_metrics", "args": {}},
            {"name": "get_service_health", "args": {"service": s.incident["service"]}},
            {"name": "query_logs", "args": {"service": s.incident["service"], "region": s.incident["region"]}},
            {"name": "check_dependencies", "args": {"service": s.incident["service"]}},
            {"name": "get_topology", "args": {"component": s.incident["service"]}},
            {"name": "search_runbooks", "args": {"query": f"{s.incident['region']} {s.incident['service']} {symptoms} troubleshooting"}},
            {"name": "search_historical_incidents", "args": {"query": symptoms, "region": s.incident["region"], "service": s.incident["service"]}},
        ]
        if "dns" in symptoms:
            plan = [x for x in plan if x["name"] != "get_topology"]
        return plan

    async def investigator(self, s: InvestigationState):
        # The model may use any read-only investigation tool; remediation tools are never offered.
        tools = [t for t in self.registry.schemas(read_only_only=True) if t["function"]["name"] != "validate_remediation"]
        offered = {t["function"]["name"] for t in tools}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": INVESTIGATOR_V1},
            {"role": "user", "content": self._investigator_context(s)},
        ]
        knowledge_tools = {"search_runbooks", "search_historical_incidents"}
        nudged = False
        for round_no in range(settings.max_tool_rounds):
            try:
                response = await self.llm.chat(messages, tools=tools)
            except Exception as exc:
                self._llm_failed(s, f"investigator_{round_no + 1}", exc)
                break
            self._record_llm(s, f"investigator_{round_no + 1}")
            if not response.tool_calls:
                consulted = {c["name"] for c in s.tool_calls} & knowledge_tools
                if not nudged and not self.llm.is_demo and consulted != knowledge_tools and round_no + 1 < settings.max_tool_rounds:
                    # Procedural check (once): the model still decides the queries, but must consult the knowledge base.
                    nudged = True
                    missing = sorted(knowledge_tools - consulted)
                    messages.append({"role": "assistant", "content": response.content or ""})
                    messages.append({"role": "user", "content": f"Before concluding, follow NOC procedure: call {', '.join(missing)} with a query describing the fault you observed."})
                    s.emit("GUARDRAIL", f"Agent asked to consult knowledge sources: {', '.join(missing)}")
                    continue
                s.investigator_summary = response.content or "Investigation evidence collected."
                break
            messages.append(self._assistant_tool_message(response))
            for call in response.tool_calls:
                content = self._execute_tool(s, call, offered)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            if self.llm.is_demo:
                break
        else:
            s.emit("GUARDRAIL", "Maximum tool-calling rounds reached")

        if not s.evidence:
            # Never leave the investigation without evidence because the model failed or chose no tools.
            s.agent_mode = "degraded" if not self.llm.is_demo else s.agent_mode
            s.emit("DEGRADED", "LLM agent gathered no evidence; executing the planned read-only tools directly")
            for i, item in enumerate(s.plan):
                self._execute_tool(s, LLMToolCall(f"plan-{i}", item["name"], dict(item.get("args") or {})), offered)
        s.emit("INVESTIGATOR", "Investigation completed using tool calls", tool_calls=len(s.tool_calls), mode=s.agent_mode)

    def _investigator_context(self, s: InvestigationState) -> str:
        safe_incident = dict(s.incident)
        symptoms_text, flagged = sanitize_user_text(" ".join(str(x) for x in safe_incident.get("symptoms", [])))
        safe_incident["symptoms"] = [symptoms_text] if flagged else safe_incident.get("symptoms", [])
        return json.dumps({
            "incident": safe_incident,
            "suggested_tools": [x["name"] for x in s.plan],
            "instructions": "Investigate this incident with the read-only tools. Gather independent evidence (telemetry, logs, topology, runbooks, historical incidents). Do not remediate.",
        }, separators=(",", ":"))

    @staticmethod
    def _assistant_tool_message(response):
        return {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in response.tool_calls
            ],
        }

    def _execute_tool(self, s: InvestigationState, call: LLMToolCall, offered: set[str]) -> str:
        """Authorize, execute, sanitize and record a tool call. Returns the (truncated) content for the model."""
        args = call.arguments if isinstance(call.arguments, dict) else {}
        signature = json.dumps([call.name, args], sort_keys=True, default=str)
        previous = next((t for t in s.tool_calls if t.get("signature") == signature), None)
        if previous is not None:
            return json.dumps({"note": "duplicate call; returning the earlier result", "result": previous["result"]}, default=str)[: settings.max_tool_result_chars]
        try:
            spec = self.registry.get(call.name)
        except KeyError:
            spec = None
        if spec is None:
            result: dict[str, Any] = {"success": False, "error": f"Unknown tool: {call.name}"}
            s.emit("TOOL_ERROR", f"Unknown tool requested: {call.name}")
        elif not spec.read_only or call.name not in offered:
            result = {"success": False, "error": f"Tool {call.name} is not authorized for investigation (read-only tools only)"}
            s.emit("GUARDRAIL", f"Blocked unauthorized tool request: {call.name}")
        else:
            result = self.registry.execute(call.name, **args)
        result, flagged = sanitize_untrusted(result)
        if flagged:
            s.emit("GUARDRAIL", f"Instruction-like content in {call.name} output was neutralised")
        success = bool(result.get("success", True)) if isinstance(result, dict) else True
        if not success:
            ERRORS.labels("tool", call.name).inc()
        self._persist_tool_call(s, call.name, args, result, signature)
        if success:
            self._ingest_tool_evidence(s, call.name, result)
        s.emit("TOOL", f"{call.name} executed", tool=call.name, success=success)
        return json.dumps(result, default=str)[: settings.max_tool_result_chars]

    def _persist_tool_call(self, s, name, args, result, signature=""):
        try:
            risk = self.registry.get(name).risk_level
        except KeyError:
            risk = "UNKNOWN"
        success = bool(result.get("success", True)) if isinstance(result, dict) else True
        self.db.add(ToolCall(
            incident_id=s.incident_id,
            tool_name=name,
            risk_level=risk,
            arguments_json=json.dumps(args, default=str),
            result_json=json.dumps(result, default=str),
            approved=False,
            success=success,
        ))
        self.db.commit()
        s.tool_calls.append({"name": name, "arguments": args, "result": result, "approved": False, "success": success, "signature": signature})

    def _ingest_tool_evidence(self, s: InvestigationState, name: str, result: dict[str, Any]):
        if name == "get_network_metrics":
            merged = dict(s.telemetry.get("metrics", {})) if isinstance(s.telemetry, dict) else {}
            merged.update(result.get("metrics", {}))
            s.telemetry = {**s.telemetry, **result, "metrics": merged}
            source_id = "telemetry-live" if not result.get("device") else f"telemetry-{result['device']}"
            s.evidence.append({"source_id": source_id, "source_type": "telemetry", "title": "Live network telemetry", "section": "metrics", "text": json.dumps(result.get("metrics", {})), "score": 1.0})
        elif name == "get_service_health":
            s.telemetry["service_health"] = result
            s.evidence.append({"source_id": "service-health-live", "source_type": "telemetry", "title": "Service health", "section": "health", "text": json.dumps(result), "score": 0.98})
        elif name == "query_logs":
            s.logs = result
            s.evidence.append({"source_id": "logs-live", "source_type": "logs", "title": "Correlated service logs", "section": "logs", "text": "\n".join(result.get("logs", [])), "score": 0.95})
        elif name == "check_dependencies":
            s.dependencies = result
            s.evidence.append({"source_id": "dependencies-live", "source_type": "topology", "title": "Dependency health", "section": "health", "text": json.dumps(result), "score": 0.92})
        elif name == "get_topology":
            s.topology = result
            s.evidence.append({"source_id": "topology-live", "source_type": "topology", "title": "Service topology", "section": "dependencies", "text": json.dumps(result), "score": 0.90})
        elif name in {"search_runbooks", "search_historical_incidents"}:
            known = {e["source_id"] for e in s.evidence}
            for row in result.get("results", []):
                source_id = row.get("chunk_id") or row.get("incident_id") or f"{name}-{len(s.evidence)}"
                if source_id in known:
                    continue
                if name == "search_historical_incidents":
                    text_value = f"Symptoms: {', '.join(row.get('symptoms', []))}. Root cause: {row.get('root_cause')}. Resolution: {row.get('resolution')}"
                else:
                    text_value = row.get("text") or json.dumps(row)
                s.evidence.append({
                    "source_id": source_id,
                    "source_type": "runbook" if name == "search_runbooks" else "historical_incident",
                    "title": row.get("title") or row.get("incident_id", source_id),
                    "section": row.get("section", ""),
                    "text": text_value,
                    "score": _float(row.get("score"), 0.7),
                })

    # ------------------------------------------------------------- bookkeeping

    def _record_llm(self, s: InvestigationState, prompt_version: str, status: str = "success"):
        try:
            usage = self.llm.last_usage
            self.db.add(LLMRequest(
                incident_id=s.incident_id,
                provider=self.llm.last_provider,
                model=self.llm.last_model,
                prompt_version=prompt_version,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                latency_ms=float(self.llm.last_latency_ms),
                status=status,
            ))
            self.db.commit()
        except Exception:
            self.db.rollback()

    def _llm_failed(self, s: InvestigationState, stage: str, exc: Exception):
        """A failed LLM request degrades one stage; it never aborts the investigation."""
        message = f"{type(exc).__name__}: {str(exc)[:300]}"
        s.llm_errors.append({"stage": stage, "error": message})
        ERRORS.labels("llm", stage.split("_")[0]).inc()
        log.warning("LLM stage failed; using fallback", extra={"extra_data": {"stage": stage, "error": message}})
        s.emit("LLM_ERROR", f"LLM unavailable during {stage}; continuing with fallback", error=message)
        self._record_llm(s, stage, status="error")

    # -------------------------------------------------------------------- RCA

    def _rca_evidence(self, s: InvestigationState, limit: int = 12) -> list[dict[str, Any]]:
        live = [e for e in s.evidence if e.get("source_type") in LIVE_SOURCE_TYPES]
        docs = sorted((e for e in s.evidence if e.get("source_type") not in LIVE_SOURCE_TYPES), key=lambda e: e.get("score", 0), reverse=True)
        return (live + docs)[:limit]

    async def rca(self, s: InvestigationState):
        evidence = self._rca_evidence(s)
        prompt_evidence = [{**e, "text": str(e.get("text", ""))[:700]} for e in evidence]
        data: dict[str, Any] = {}
        method = "rule_based_fallback"
        if not self.llm.is_demo:
            try:
                data = await self.llm.generate_json(RCA_V1, json.dumps({"incident": s.incident, "evidence": prompt_evidence}, default=str), {
                    "root_cause": "",
                    "confidence": 0.0,
                    "alternative_causes": [""],
                    "affected_components": [""],
                    "evidence_ids": [""],
                    "summary": "",
                })
                self._record_llm(s, "rca_v1")
            except Exception as exc:
                self._llm_failed(s, "rca", exc)
                data = {}
        if isinstance(data, dict) and str(data.get("root_cause") or "").strip():
            method = "llm"
        else:
            data = self._fallback_rca(s)
        evidence_by_id = {e["source_id"]: e for e in s.evidence}
        cited = data.get("evidence_ids") if isinstance(data.get("evidence_ids"), list) else []
        selected = [evidence_by_id[str(x)] for x in cited if str(x) in evidence_by_id]
        if not selected:
            selected = evidence[:5]
        s.rca = {
            "root_cause": str(data.get("root_cause", "insufficient evidence")),
            "confidence": round(max(0.0, min(_float(data.get("confidence")), 0.99)), 2),
            "alternative_causes": [str(x) for x in (data.get("alternative_causes") or []) if str(x).strip()][:3],
            "affected_components": [str(x) for x in (data.get("affected_components") or []) if str(x).strip()],
            "evidence": selected,
            "cited_evidence_ids": [str(x) for x in cited],
            "summary": str(data.get("summary", "Evidence-backed RCA generated.")),
            "method": method,
            "model": self.llm.last_model if method == "llm" else "rules",
        }
        s.emit("RCA", f"Root cause analysis completed: {s.rca['root_cause']}", confidence=s.rca["confidence"], method=method)

    def _fallback_rca(self, s: InvestigationState) -> dict[str, Any]:
        """Transparent rule-based RCA used only when no LLM result is available (demo mode or provider failure)."""
        metrics = s.telemetry.get("metrics", {}) if isinstance(s.telemetry, dict) else {}
        api = metrics.get("api-mum-01", {})
        service_health = s.telemetry.get("service_health", {}) if isinstance(s.telemetry, dict) else {}
        if not api and isinstance(service_health, dict):
            api = service_health.get("metrics", {})
        router = metrics.get("router-mum-04", {})
        dns = metrics.get("dns-mum-01", {})
        db = metrics.get("db-mum-01", {})
        symptoms = " ".join(s.incident["symptoms"]).lower()
        if router.get("throughput_mbps", 0) >= 900 and db.get("query_latency_ms", 0) > 500 and router.get("packet_loss_pct", 0) < 10:
            root, confidence = ROOT_LABELS["complex"], 0.86
        elif router.get("packet_loss_pct", 0) >= 10:
            root, confidence = ROOT_LABELS["router-overload"], 0.93
        elif dns.get("dns_latency_ms", 0) > 500:
            root, confidence = ROOT_LABELS["dns-failure"], 0.91
        elif db.get("query_latency_ms", 0) > 500:
            root, confidence = ROOT_LABELS["database-latency"], 0.92
        elif api.get("healthy", True) is False or any("process exited" in str(x).lower() for x in s.logs.get("logs", [])):
            root, confidence = ROOT_LABELS["service-crash"], 0.96
        elif router.get("throughput_mbps", 0) >= 1000:
            root, confidence = ROOT_LABELS["congestion"], 0.88
        elif "authentication" in symptoms:
            root, confidence = ROOT_LABELS["auth-degradation"], 0.81
        elif any(x in symptoms for x in ("backend overload", "load balancer", "imbalance")) and api.get("cpu", 0) >= 90:
            root, confidence = ROOT_LABELS["lb-imbalance"], 0.84
        else:
            root, confidence = "insufficient evidence", 0.42
        alts = []
        if root != ROOT_LABELS["router-overload"] and "latency" in symptoms:
            alts.append(ROOT_LABELS["router-overload"])
        return {
            "root_cause": root,
            "confidence": confidence,
            "alternative_causes": alts,
            "affected_components": s.topology.get("dependencies", []),
            "evidence_ids": [e["source_id"] for e in self._rca_evidence(s, 5)],
            "summary": f"Observed telemetry and operational evidence support {root}.",
        }

    # ------------------------------------------------------------ remediation

    async def remediation(self, s: InvestigationState):
        allowed = [
            {"name": t.name, "risk": t.risk_level, "reversible": t.reversible, "description": t.description}
            for t in self.registry.list() if not t.read_only
        ]
        target_allowlist = {
            "restart_service": {"api-mum-01"},
            "restart_dns": {"dns-mum-01"},
            "redistribute_traffic": {"router-mum-04"},
            "scale_service": {"api-mum-01", "db-mum-01"},
            "create_ticket": {"NOC", s.incident["service"]},
        }
        data: dict[str, Any] = {}
        if not self.llm.is_demo:
            try:
                data = await self.llm.generate_json(
                    REMEDIATION_V1,
                    json.dumps({"incident": s.incident, "rca": {k: v for k, v in s.rca.items() if k != "evidence"},
                                "allowed_actions": allowed, "allowed_targets": {k: sorted(v) for k, v in target_allowlist.items()}}, default=str),
                    {"action": "", "target": "", "reason": "", "risk_level": "", "reversible": True, "expected_impact": "", "requires_approval": True},
                )
                self._record_llm(s, "remediation_v1")
            except Exception as exc:
                self._llm_failed(s, "remediation", exc)
                data = {}
        mapping = {
            ROOT_LABELS["router-overload"]: ("redistribute_traffic", "router-mum-04", "Reduce router saturation and packet loss."),
            ROOT_LABELS["congestion"]: ("redistribute_traffic", "router-mum-04", "Reduce interface saturation."),
            ROOT_LABELS["complex"]: ("redistribute_traffic", "router-mum-04", "Relieve network saturation first; database follow-up may be needed."),
            ROOT_LABELS["dns-failure"]: ("restart_dns", "dns-mum-01", "Restore resolver health and DNS latency."),
            ROOT_LABELS["service-crash"]: ("restart_service", "api-mum-01", "Restore service availability."),
            ROOT_LABELS["database-latency"]: ("scale_service", "db-mum-01", "Reduce database saturation."),
            ROOT_LABELS["lb-imbalance"]: ("scale_service", "api-mum-01", "Reduce overloaded backend utilization."),
            ROOT_LABELS["auth-degradation"]: ("scale_service", "api-mum-01", "Reduce downstream pressure."),
        }
        from app.evaluation.evaluator import canonical_root_cause
        fallback = mapping.get(canonical_root_cause(s.rca.get("root_cause", "")), ("create_ticket", "NOC", "Escalate for manual investigation."))
        action = str(data.get("action") or "")
        target = str(data.get("target") or "")
        method = "llm"
        if action not in target_allowlist or target not in target_allowlist[action]:
            # Model output outside the action/target allowlist is never executed; use the policy mapping instead.
            if data:
                s.emit("GUARDRAIL", f"Model proposed {action or 'no action'} on {target or 'no target'} outside the allowlist; using policy mapping")
            action, target, method = fallback[0], fallback[1], "policy_mapping"
        spec = self.registry.get(action)
        s.remediation = {
            "action": action,
            "target": target,
            "reason": str(data.get("reason") or s.rca.get("summary", "")) if method == "llm" else s.rca.get("summary", ""),
            "risk_level": spec.risk_level,
            "reversible": spec.reversible,
            "expected_impact": str(data.get("expected_impact") or fallback[2]) if method == "llm" else fallback[2],
            "requires_approval": True,
            "method": method,
        }
        s.emit("REMEDIATION", f"Prepared remediation: {action} on {target} (awaiting human approval)", risk=spec.risk_level, method=method)

    # ----------------------------------------------------------------- report

    async def report(self, s: InvestigationState):
        payload = {"incident": s.incident, "timeline": [{"event": e["event_type"], "message": e["message"]} for e in s.events],
                   "rca": {**s.rca, "evidence": [{"source_id": e["source_id"], "title": e["title"], "text": str(e["text"])[:300]} for e in s.rca.get("evidence", [])]},
                   "remediation": s.remediation}
        s.final_report = ""
        if not self.llm.is_demo and not s.llm_errors:
            try:
                response = await self.llm.chat([{"role": "system", "content": REPORT_V1}, {"role": "user", "content": json.dumps(payload, default=str)}])
                self._record_llm(s, "report_v1")
                s.final_report = response.content
            except Exception as exc:
                self._llm_failed(s, "report", exc)
        if not s.final_report:
            s.final_report = self._fallback_report(s)
        s.emit("REPORT", "Incident investigation report generated")

    @staticmethod
    def _fallback_report(s: InvestigationState) -> str:
        return (
            f"# NOCMind Incident Report\n\n"
            f"**Incident:** {s.incident_id} — {s.incident.get('title', 'unknown')}\n"
            f"**Region:** {s.incident['region']}\n**Service:** {s.incident['service']}\n"
            f"**Severity:** {s.incident['severity']}\n**Agent mode:** {s.agent_mode}\n\n"
            f"## Root Cause\n{s.rca.get('root_cause', 'insufficient evidence')} ({round(float(s.rca.get('confidence', 0)) * 100)}% confidence, {s.rca.get('method', 'unknown')})\n\n"
            f"## Summary\n{s.rca.get('summary', 'Insufficient evidence.')}\n\n"
            f"## Evidence\n" + "".join(f"- {e['title']} ({e['source_id']})\n" for e in s.rca.get("evidence", [])) +
            f"\n## Remediation Plan (requires approval)\n{s.remediation.get('action', 'Escalate')} → {s.remediation.get('target', 'N/A')}\n"
        )

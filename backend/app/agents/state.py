from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InvestigationState:
    incident_id: str
    incident: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    logs: dict[str, Any] = field(default_factory=dict)
    topology: dict[str, Any] = field(default_factory=dict)
    dependencies: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    rca: dict[str, Any] = field(default_factory=dict)
    remediation: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    investigator_summary: str = ""
    final_report: str = ""
    step_count: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    llm_errors: list[dict[str, Any]] = field(default_factory=list)
    # llm: tools chosen by the model; degraded: model unavailable/no tools, planned tools ran directly; demo: offline mode
    agent_mode: str = "llm"

    def emit(self, event_type: str, message: str, **details):
        self.events.append({"event_type": event_type, "message": message, "details": details})
        self.step_count += 1

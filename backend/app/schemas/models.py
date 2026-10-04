from typing import Any

from pydantic import BaseModel, Field


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    region: str = Field(min_length=1, max_length=64)
    severity: str = Field('SEV-2', pattern=r'^SEV-[1-4]$')
    service: str = Field(min_length=1, max_length=128)
    symptoms: list[str] = Field(default_factory=list, max_length=20)
    ground_truth_root_cause: str = Field('', max_length=255)

class Evidence(BaseModel):
    source_id: str
    source_type: str
    title: str
    section: str = ''
    text: str
    score: float

class RCAResponse(BaseModel):
    root_cause: str
    confidence: float
    alternative_causes: list[str]
    evidence: list[Evidence]
    affected_components: list[str]
    summary: str

class RemediationPlan(BaseModel):
    action: str
    target: str
    reason: str
    risk_level: str
    reversible: bool
    expected_impact: str
    requires_approval: bool = True

class InvestigationResponse(BaseModel):
    incident_id: str
    status: str
    rca: RCAResponse | None = None
    remediation: RemediationPlan | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    final_report: str = ""

class RAGQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(8, ge=1, le=50)
    rerank_k: int = Field(5, ge=1, le=20)

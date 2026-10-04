from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32), default='VIEWER')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Incident(Base):
    __tablename__ = 'incidents'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    service: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default='NEW')
    symptoms_json: Mapped[str] = mapped_column(Text, default='[]')
    ground_truth_root_cause: Mapped[str] = mapped_column(String(255), default='')
    rca_json: Mapped[str] = mapped_column(Text, default='{}')
    remediation_json: Mapped[str] = mapped_column(Text, default='{}')
    final_report: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class IncidentEvent(Base):
    __tablename__ = 'incident_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    event_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str] = mapped_column(Text, default='{}')


class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(64))
    service: Mapped[str] = mapped_column(String(128), default='')
    severity: Mapped[str] = mapped_column(String(16), default='')
    version: Mapped[str] = mapped_column(String(32), default='1.0')


class DocumentChunk(Base):
    __tablename__ = 'document_chunks'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(String(255), default='')
    metadata_json: Mapped[str] = mapped_column(Text, default='{}')
    embedding_json: Mapped[str] = mapped_column(Text, default='[]')


class HistoricalIncident(Base):
    __tablename__ = 'historical_incidents'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    region: Mapped[str] = mapped_column(String(64))
    service: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(16))
    symptoms_json: Mapped[str] = mapped_column(Text, default='[]')
    root_cause: Mapped[str] = mapped_column(String(255))
    resolution: Mapped[str] = mapped_column(Text)


class ToolCall(Base):
    __tablename__ = 'tool_calls'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    risk_level: Mapped[str] = mapped_column(String(32))
    arguments_json: Mapped[str] = mapped_column(Text, default='{}')
    result_json: Mapped[str] = mapped_column(Text, default='{}')
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class RemediationAction(Base):
    __tablename__ = 'remediation_actions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(128))
    target: Mapped[str] = mapped_column(String(255))
    risk_level: Mapped[str] = mapped_column(String(32))
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default='PROPOSED')
    details_json: Mapped[str] = mapped_column(Text, default='{}')
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LLMRequest(Base):
    __tablename__ = 'llm_requests'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(32), default='success')


class EvaluationRun(Base):
    __tablename__ = 'evaluation_runs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    metrics_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    incident_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128))
    details_json: Mapped[str] = mapped_column(Text, default='{}')
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

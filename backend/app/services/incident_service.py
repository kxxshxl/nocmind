from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agents.graph import InvestigationGraph
from app.core.security import audit
from app.core.telemetry import ACTIVE_INCIDENTS, ERRORS, INVESTIGATIONS, REMEDIATIONS
from app.db.models import Incident, IncidentEvent, RemediationAction, ToolCall, User
from app.tools.default_registry import build_registry
from app.tools.simulator_tools import validate_remediation


class IncidentService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, payload):
        incident_id = f"INC-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[-10:]}"
        inc = Incident(id=incident_id, title=payload.title, region=payload.region, severity=payload.severity,
                       service=payload.service, status='NEW', symptoms_json=json.dumps(payload.symptoms),
                       ground_truth_root_cause=getattr(payload, 'ground_truth_root_cause', ''))
        self.db.add(inc)
        self.db.add(IncidentEvent(incident_id=incident_id, event_type='CREATED', message='Incident created', details_json='{}'))
        self.db.commit()
        return inc

    def list(self):
        return self.db.query(Incident).order_by(Incident.created_at.desc()).all()

    def get(self, incident_id):
        return self.db.get(Incident, incident_id)

    def _refresh_active_gauge(self):
        ACTIVE_INCIDENTS.set(self.db.query(Incident).filter(Incident.status.notin_(['RESOLVED', 'ESCALATED'])).count())

    async def investigate(self, incident_id, user: User | None = None):
        inc = self.get(incident_id)
        if not inc:
            raise ValueError('Incident not found')
        inc.status = 'INVESTIGATING'
        if user is not None:
            audit(self.db, user.id, 'investigate', incident_id)
        self.db.commit()
        try:
            state = await InvestigationGraph(self.db).run(incident_id)
        except Exception as exc:
            # Unexpected agent failure: never leave the incident stuck in INVESTIGATING.
            self.db.rollback()
            ERRORS.labels('agent', type(exc).__name__).inc()
            INVESTIGATIONS.labels('error', 'none').inc()
            inc = self.get(incident_id)
            inc.status = 'ESCALATED'
            self.db.add(IncidentEvent(incident_id=incident_id, event_type='ERROR', message='Investigation failed; escalated to a human operator',
                                      details_json=json.dumps({'error': f'{type(exc).__name__}: {str(exc)[:300]}'})))
            self.db.commit()
            self._refresh_active_gauge()
            raise
        for e in state.events:
            self.db.add(IncidentEvent(incident_id=incident_id, event_type=e['event_type'], message=e['message'], details_json=json.dumps(e.get('details', {}), default=str)))
        inc.status = 'REMEDIATION_PENDING' if state.rca and state.remediation else ('ROOT_CAUSE_IDENTIFIED' if state.rca else 'ESCALATED')
        inc.rca_json = json.dumps(state.rca or {}, default=str)
        inc.remediation_json = json.dumps(state.remediation or {}, default=str)
        inc.final_report = state.final_report or ''
        if state.remediation:
            self.db.add(RemediationAction(
                incident_id=incident_id,
                action=state.remediation['action'], target=state.remediation['target'],
                risk_level=state.remediation['risk_level'], reversible=state.remediation['reversible'],
                details_json=json.dumps(state.remediation, default=str), status='PROPOSED'
            ))
        self.db.commit()
        INVESTIGATIONS.labels(state.agent_mode, state.rca.get('method', 'none')).inc()
        self._refresh_active_gauge()
        return inc, state

    def approve_remediation(self, incident_id: str, user: User):
        inc = self.get(incident_id)
        if not inc:
            raise ValueError('Incident not found')
        action = self.db.query(RemediationAction).filter(RemediationAction.incident_id == incident_id).order_by(RemediationAction.id.desc()).first()
        if not action:
            raise ValueError('No remediation proposed')
        if action.status not in {'PROPOSED', 'REJECTED'}:
            raise ValueError(f'Action cannot be approved from {action.status}')
        action.status = 'APPROVED'
        action.approved_by = user.id
        action.approved_at = datetime.now(timezone.utc)
        inc.status = 'REMEDIATION_APPROVED'
        audit(self.db, user.id, 'approve_remediation', incident_id, action_id=action.id, tool=action.action, target=action.target)
        self.db.add(IncidentEvent(incident_id=incident_id, event_type='APPROVAL', message=f'Remediation approved by {user.username}', details_json=json.dumps({'action_id': action.id})))
        self.db.commit()
        return {'action_id': action.id, 'status': action.status, 'approved_by': user.username}

    def execute_remediation(self, incident_id: str, user: User):
        inc = self.get(incident_id)
        if not inc:
            raise ValueError('Incident not found')
        action = self.db.query(RemediationAction).filter(RemediationAction.incident_id == incident_id).order_by(RemediationAction.id.desc()).first()
        if not action:
            raise ValueError('No remediation proposed')
        if action.status != 'APPROVED':
            raise ValueError('Remediation must be explicitly approved before execution')
        registry = build_registry(self.db)
        try:
            spec = registry.get(action.action)
        except KeyError:
            raise ValueError('Unknown remediation tool')
        if spec.read_only:
            raise ValueError('Read-only tool cannot execute remediation')
        inc.status = 'REMEDIATING'
        result = registry.execute(action.action, approved=True, target=action.target)
        self.db.add(ToolCall(incident_id=incident_id, tool_name=action.action, risk_level=action.risk_level,
                             arguments_json=json.dumps({'target': action.target}), result_json=json.dumps(result, default=str), approved=True,
                             success=bool(result.get('success'))))
        action.status = 'EXECUTED' if result.get('success') else 'FAILED'
        validation = validate_remediation(action.target)
        inc.status = 'RESOLVED' if validation['success'] else 'ESCALATED'
        action.status = 'VALIDATED' if validation['success'] else action.status
        event_type = 'VALIDATED' if validation['success'] else 'VALIDATION_FAILED'
        self.db.add(IncidentEvent(incident_id=incident_id, event_type=event_type,
                                  message='Remediation validated against simulator telemetry' if validation['success'] else 'Remediation failed validation',
                                  details_json=json.dumps(validation, default=str)))
        audit(self.db, user.id, 'execute_remediation', incident_id, action=action.action, target=action.target,
              execution_success=bool(result.get('success')), validated=bool(validation['success']))
        self.db.commit()
        REMEDIATIONS.labels(action.action, 'validated' if validation['success'] else ('executed' if result.get('success') else 'failed')).inc()
        self._refresh_active_gauge()
        return {'success': bool(validation['success']), 'action': action.action, 'target': action.target, 'execution': result, 'validation': validation, 'status': inc.status}

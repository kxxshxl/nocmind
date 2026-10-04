from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import OPERATORS, audit, current_user, require_roles
from app.db.models import IncidentEvent, ToolCall, User
from app.db.session import get_db
from app.schemas.models import IncidentCreate
from app.services.incident_service import IncidentService

router = APIRouter(prefix='/incidents', tags=['incidents'])
log = logging.getLogger('nocmind.api')


def serialize(i):
    return {
        'id': i.id, 'title': i.title, 'region': i.region, 'severity': i.severity,
        'service': i.service, 'status': i.status, 'symptoms': json.loads(i.symptoms_json or '[]'),
        'rca': json.loads(i.rca_json or '{}'),
        'remediation': json.loads(i.remediation_json or '{}'),
        'final_report': i.final_report or '',
        'created_at': i.created_at.isoformat() if i.created_at else None,
    }


@router.post('', status_code=201)
def create(payload: IncidentCreate, db: Session = Depends(get_db), user: User = Depends(require_roles(*OPERATORS))):
    inc = IncidentService(db).create(payload)
    audit(db, user.id, 'create_incident', inc.id)
    db.commit()
    return serialize(inc)


@router.get('')
def list_incidents(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [serialize(i) for i in IncidentService(db).list()]


@router.get('/{incident_id}')
def get_incident(incident_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    i = IncidentService(db).get(incident_id)
    if not i:
        raise HTTPException(404, 'Incident not found')
    return serialize(i)


@router.post('/{incident_id}/investigate')
async def investigate(incident_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles(*OPERATORS))):
    try:
        i, state = await IncidentService(db).investigate(incident_id, user)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception:
        log.exception('Investigation failed')
        raise HTTPException(500, 'Investigation failed; the incident was escalated to a human operator')
    tool_calls = [{k: v for k, v in t.items() if k != 'signature'} for t in state.tool_calls]
    return {'incident_id': i.id, 'status': i.status, 'rca': state.rca, 'remediation': state.remediation, 'events': state.events,
            'tool_calls': tool_calls, 'final_report': state.final_report, 'agent_mode': state.agent_mode, 'llm_errors': state.llm_errors}


@router.get('/{incident_id}/timeline')
def timeline(incident_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not IncidentService(db).get(incident_id):
        raise HTTPException(404, 'Incident not found')
    return [
        {'timestamp': e.timestamp.isoformat(), 'event_type': e.event_type, 'message': e.message, 'details': json.loads(e.details_json or '{}')}
        for e in db.query(IncidentEvent).filter(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.id.asc())
    ]


@router.get('/{incident_id}/evidence')
def evidence(incident_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    service = IncidentService(db)
    if not service.get(incident_id):
        raise HTTPException(404, 'Incident not found')
    events = db.query(IncidentEvent).filter(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.id.asc()).all()
    tool_calls = db.query(ToolCall).filter(ToolCall.incident_id == incident_id).order_by(ToolCall.id.asc()).all()
    return {
        'events': [{'event_type': e.event_type, 'message': e.message, 'details': json.loads(e.details_json or '{}')} for e in events],
        'tool_calls': [{'tool_name': t.tool_name, 'risk_level': t.risk_level, 'arguments': json.loads(t.arguments_json or '{}'), 'result': json.loads(t.result_json or '{}'), 'approved': t.approved, 'success': t.success} for t in tool_calls],
    }


@router.post('/{incident_id}/approve-remediation')
def approve(incident_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles(*OPERATORS))):
    try:
        action = IncidentService(db).approve_remediation(incident_id, user)
        return action
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post('/{incident_id}/execute-remediation')
def execute(incident_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles(*OPERATORS))):
    try:
        result = IncidentService(db).execute_remediation(incident_id, user)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    audit,
    create_access_token,
    current_user,
    hash_password,
    login_throttle,
    require_roles,
    verify_password,
)
from app.db.models import AuditEvent, User
from app.db.session import get_db

router = APIRouter(prefix='/auth', tags=['auth'])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post('/login')
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    if login_throttle.locked(payload.username):
        raise HTTPException(429, 'Too many failed login attempts; try again later')
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        login_throttle.fail(payload.username)
        audit(db, user.id if user else 'anonymous', 'login_failed', username=payload.username)
        db.commit()
        raise HTTPException(401, 'Invalid username or password')
    login_throttle.reset(payload.username)
    audit(db, user.id, 'login', username=user.username)
    db.commit()
    return {'access_token': create_access_token(user), 'token_type': 'bearer', 'role': user.role, 'username': user.username}


@router.get('/me')
def me(user: User = Depends(current_user)):
    return {'id': user.id, 'username': user.username, 'role': user.role}


@router.get('/audit')
def audit_log(limit: int = 100, db: Session = Depends(get_db), user: User = Depends(require_roles('ADMIN'))):
    rows = db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(max(limit, 1), 500))
    return [{'id': r.id, 'timestamp': r.timestamp.isoformat(), 'actor_id': r.actor_id, 'incident_id': r.incident_id,
             'action': r.action, 'details': json.loads(r.details_json or '{}')} for r in rows]


def ensure_demo_user(db: Session) -> None:
    accounts = [(settings.demo_username, settings.demo_password, 'ADMIN'),
                ('operator', settings.demo_operator_password, 'OPERATOR'),
                ('viewer', settings.demo_viewer_password, 'VIEWER')]
    for username, password, role in accounts:
        if not password or db.query(User).filter(User.username == username).first():
            continue
        db.add(User(id=str(uuid.uuid4()), username=username, password_hash=hash_password(password), role=role))
    db.commit()

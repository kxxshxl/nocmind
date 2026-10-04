from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import OPERATORS, audit, current_user, require_roles
from app.db.models import User
from app.db.session import get_db
from app.tools.simulator_tools import simulator

router = APIRouter(prefix='/simulator', tags=['simulator'])

@router.get('/state')
def state(user: User = Depends(current_user)):
    return simulator.snapshot()

@router.post('/scenarios/{scenario}')
def trigger(scenario: str, db: Session = Depends(get_db), user: User = Depends(require_roles(*OPERATORS))):
    try:
        result = simulator.trigger(scenario)
    except ValueError as e:
        raise HTTPException(404, str(e))
    audit(db, user.id, 'simulator_trigger', scenario=scenario)
    db.commit()
    return result

@router.post('/reset')
def reset(db: Session = Depends(get_db), user: User = Depends(require_roles(*OPERATORS))):
    simulator.reset()
    audit(db, user.id, 'simulator_reset')
    db.commit()
    return simulator.snapshot()

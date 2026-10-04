from fastapi import APIRouter, Depends

from app.core.security import current_user
from app.db.models import User
from app.tools.simulator_tools import get_topology

router = APIRouter(prefix='/topology', tags=['topology'])

@router.get('')
def topology(component: str = 'API Gateway', user: User = Depends(current_user)):
    return get_topology(component)

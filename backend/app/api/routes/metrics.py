from fastapi import APIRouter

from app.core.telemetry import metrics_response

router = APIRouter(tags=['observability'])


@router.get('/metrics')
def metrics():
    return metrics_response()

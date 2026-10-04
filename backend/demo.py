import asyncio
import json
from app.db.session import Base, engine, SessionLocal
from app.tools.simulator_tools import simulator
from app.services.incident_service import IncidentService
from app.api.routes.auth import ensure_demo_user


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    ensure_demo_user(db)
    simulator.trigger('router-overload')
    service = IncidentService(db)
    inc = service.create(type('Payload', (), {
        'title': 'Mumbai API Gateway latency spike',
        'region': 'Mumbai', 'severity': 'SEV-2', 'service': 'API Gateway',
        'symptoms': ['high latency', 'packet loss', 'router saturation'],
        'ground_truth_root_cause': 'edge router overload'
    })())
    _, state = asyncio.run(service.investigate(inc.id))
    print('\n=== NOCMind Demo ===')
    print(json.dumps({'incident': inc.id, 'rca': state.rca, 'remediation': state.remediation, 'tool_calls': state.tool_calls}, indent=2))
    user = db.query(__import__('app.db.models', fromlist=['User']).User).filter_by(username='admin').first()
    service.approve_remediation(inc.id, user)
    result = service.execute_remediation(inc.id, user)
    print('\n=== VALIDATION ===')
    print(json.dumps(result, indent=2))
    db.close()


if __name__ == '__main__':
    main()

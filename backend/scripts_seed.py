from pathlib import Path
import json
from app.db.session import Base, engine, SessionLocal
from app.db.models import HistoricalIncident
from app.rag.pipeline import RAGPipeline
from datetime import datetime, timezone, timedelta
from app.tools.simulator_tools import simulator

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'

DOCS = {
'RB-ROUTER-001': ('Router Overload Runbook', 'runbook', 'Network', 'SEV-2', '''# Symptoms\nRouter overload commonly presents as rising CPU, saturated interfaces, increased queue depth, packet loss and elevated downstream latency.\n# Diagnosis\nInspect router CPU, throughput, packet loss and dependent service latency. Correlate the start of saturation with the incident timeline. Check whether traffic is concentrated on one edge router.\n# Remediation\nFor a saturated edge router, redistribute traffic to healthy peers where safe. Preserve routing policy and validate packet loss and latency after the change.\n# Validation\nSuccessful recovery should reduce CPU below 70 percent, packet loss below 2 percent and application latency below 150 ms.'''),
'RB-DNS-001': ('DNS Failure Runbook', 'runbook', 'DNS', 'SEV-2', '''# Symptoms\nDNS incidents present as elevated name resolution latency, DNS timeout errors and cascading application request failures.\n# Diagnosis\nInspect resolver health, timeout rate, CPU and recent resolver logs. Check whether application latency closely follows DNS latency.\n# Remediation\nRestart an unhealthy resolver when policy allows. Verify that the resolver returns successful queries and application latency recovers.'''),
'RB-DB-001': ('Database Latency Runbook', 'runbook', 'Database', 'SEV-2', '''# Symptoms\nDatabase degradation causes high query latency, connection pool saturation and elevated application latency.\n# Diagnosis\nInspect database CPU, query latency and connection pressure. Correlate application latency with database latency.\n# Remediation\nScale database capacity or reduce workload according to operating policy. Validate query latency and service response time.'''),
'RB-SVC-001': ('Service Crash Runbook', 'runbook', 'API Gateway', 'SEV-1', '''# Symptoms\nA crashed service shows failed health checks, near-zero healthy instances and very high error rates.\n# Diagnosis\nCheck process health, error logs and dependency state.\n# Remediation\nRestart the affected service when the action is permitted. Validate health checks and error rate afterward.'''),
'RB-CONG-001': ('Network Congestion Runbook', 'runbook', 'Network', 'SEV-2', '''# Symptoms\nCongestion produces high throughput utilization, elevated latency, queue depth and packet loss.\n# Diagnosis\nInspect saturated interfaces and compare them with peer links.\n# Remediation\nRedistribute traffic when alternate capacity exists, then validate interface utilization and packet loss.'''),
}

HIST = [
('HIST-1001','Mumbai','API Gateway','SEV-2',['high latency','packet loss'],'edge router overload','Traffic redistributed from a saturated edge router.'),
('HIST-1002','Mumbai','API Gateway','SEV-2',['high latency','DNS timeout'],'DNS resolver failure','Resolver restarted and DNS health restored.'),
('HIST-1003','Chennai','Billing','SEV-2',['database latency','slow queries'],'database performance degradation','Database capacity increased.'),
('HIST-1004','Delhi','API Gateway','SEV-1',['service unavailable','error spike'],'API service crash','Failed application instance restarted.'),
('HIST-1005','Bangalore','API Gateway','SEV-2',['high latency','throughput saturation'],'network congestion','Traffic redistributed to peer links.'),
]

SCENARIOS = [
('router-overload','Mumbai API Gateway latency spike','Mumbai','SEV-2','API Gateway',['high latency','packet loss','router saturation'],'edge router overload'),
('dns-failure','Mumbai DNS resolution failure','Mumbai','SEV-2','API Gateway',['DNS timeout','high DNS latency','application errors'],'DNS resolver failure'),
('database-latency','Mumbai database degradation','Mumbai','SEV-2','API Gateway',['database latency','slow queries','high API latency'],'database performance degradation'),
('service-crash','Mumbai API service crash','Mumbai','SEV-1','API Gateway',['service unavailable','error spike'],'API service crash'),
('congestion','Mumbai network congestion','Mumbai','SEV-2','API Gateway',['high latency','packet loss','throughput saturation'],'network congestion'),
('lb-imbalance','Mumbai load balancer imbalance','Mumbai','SEV-2','API Gateway',['backend overload','high latency'],'load balancer imbalance'),
('auth-degradation','Mumbai authentication degradation','Mumbai','SEV-2','API Gateway',['authentication latency','application errors'],'authentication dependency degradation'),
('complex','Mumbai cascading degradation','Mumbai','SEV-1','API Gateway',['high latency','packet loss','database latency'],'combined network saturation and database degradation'),
]

def main():
    (DATA/'runbooks').mkdir(parents=True, exist_ok=True)
    (DATA/'incidents').mkdir(parents=True, exist_ok=True)
    (DATA/'telemetry').mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    for doc_id, (title, kind, service, severity, body) in DOCS.items():
        (DATA/'runbooks'/f'{doc_id}.md').write_text(f'# {title}\n\n{body}\n', encoding='utf-8')
        RAGPipeline(db).ingest_document(doc_id, title, kind, service, severity, '1.0', body)
    for hid, region, service, severity, symptoms, root, resolution in HIST:
        if not db.get(HistoricalIncident, hid):
            db.add(HistoricalIncident(id=hid, timestamp=datetime.now(timezone.utc)-timedelta(days=20), region=region, service=service, severity=severity,
                                       symptoms_json=json.dumps(symptoms), root_cause=root, resolution=resolution))
        body = f'''# Historical incident {hid}\nRegion: {region}\nService: {service}\nSeverity: {severity}\nSymptoms: {', '.join(symptoms)}\n# Root Cause\n{root}\n# Resolution\n{resolution}'''
        RAGPipeline(db).ingest_document(hid, f'Historical Incident {hid}', 'historical_incident', service, severity, '1.0', body)
    db.commit()
    print('Seeded documents and historical incidents.')
    print('Scenarios:', ', '.join(s[0] for s in SCENARIOS))
    db.close()

if __name__ == '__main__':
    main()

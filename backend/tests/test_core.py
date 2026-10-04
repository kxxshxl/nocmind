import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.graph import InvestigationGraph
from app.db.models import Incident
from app.db.session import Base
from app.rag.pipeline import RAGPipeline


def db_session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/test.db', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_rag_pipeline(tmp_path):
    db = db_session(tmp_path)
    rag = RAGPipeline(db)
    rag.ingest_document('DOC1','Router Runbook','runbook','Network','SEV-2','1.0','''# Diagnosis\nRouter saturation causes packet loss and high latency.\n# Remediation\nRedistribute traffic.''')
    rows = rag.query('router packet loss latency', 5, 3)
    assert rows and rows[0].document_id == 'DOC1'


def test_investigation(tmp_path):
    db = db_session(tmp_path)
    inc = Incident(id='INC-1', title='test', region='Mumbai', severity='SEV-2', service='API Gateway', symptoms_json=json.dumps(['high latency','packet loss']))
    db.add(inc)
    db.commit()
    rag = RAGPipeline(db)
    rag.ingest_document('RB-1','Router Runbook','runbook','Network','SEV-2','1.0','''# Diagnosis\nRouter saturation causes packet loss and high latency.''')
    from app.tools.simulator_tools import simulator
    simulator.trigger('router-overload')
    state = asyncio.run(InvestigationGraph(db).run('INC-1'))
    assert state.rca['root_cause'] == 'edge router overload'
    assert state.remediation['action'] == 'redistribute_traffic'

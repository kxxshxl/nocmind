"""Smoke-check every simulator scenario end to end, then run the benchmark.

Usage: PYTHONPATH=. python scripts/verify.py
"""
from __future__ import annotations

import asyncio
import json

from app.agents.graph import InvestigationGraph
from app.db.models import Incident
from app.db.session import Base, SessionLocal, engine
from app.evaluation.run_benchmark import SCENARIOS
from app.evaluation.run_benchmark import run_async as run_benchmark
from app.tools.simulator_tools import simulator


async def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    rows = []
    for scenario, title, symptoms, expected, _ in SCENARIOS:
        simulator.trigger(scenario)
        iid = f"VERIFY-{scenario}"
        old = db.get(Incident, iid)
        if old:
            db.delete(old)
            db.commit()
        db.add(Incident(id=iid, title=title, region="Mumbai", severity="SEV-2", service="API Gateway", symptoms_json=json.dumps(symptoms)))
        db.commit()
        state = await InvestigationGraph(db).run(iid)
        rows.append({"scenario": scenario, "expected": expected, "root_cause": state.rca.get("root_cause"),
                     "confidence": state.rca.get("confidence"), "rca_method": state.rca.get("method"),
                     "agent_mode": state.agent_mode, "tools": len(state.tool_calls)})
    db.close()
    benchmark = await run_benchmark()
    print(json.dumps({"scenarios": rows, "benchmark": benchmark}, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())

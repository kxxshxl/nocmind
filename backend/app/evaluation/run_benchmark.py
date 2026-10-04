"""NOCMind benchmark.

Deterministic metrics (ground truth): retrieval, RCA accuracy, tool selection, citation validity, latency.
Model-judged metrics (LLM-as-judge): faithfulness, hallucination rate, answer relevance.

Each scenario is investigated once and each RCA is judged once, so a run costs a bounded number of LLM calls.
With LLM_PROVIDER=ollama (and EVAL_JUDGE_PROVIDER=ollama) the run needs no external API quota.

CLI:  python -m app.evaluation.run_benchmark [--provider ollama] [--judge ollama|none] [--scenarios a,b]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from datetime import datetime, timezone
from statistics import mean

from app.agents.graph import InvestigationGraph
from app.core.config import settings
from app.db.models import DocumentChunk, EvaluationRun, Incident, LLMRequest
from app.db.session import Base, SessionLocal, engine
from app.evaluation.evaluator import Evaluator, canonical_root_cause, citation_metrics
from app.rag.pipeline import RAGPipeline
from app.rag.retriever import HybridRetriever, RetrievalResult, cosine
from app.services.llm import LLMClient
from app.tools.default_registry import build_registry
from app.tools.simulator_tools import simulator, validate_remediation

# Ground-truth remediation (action, target) per scenario; None means no single action is sufficient.
EXPECTED_REMEDIATION = {
    "router-overload": ("redistribute_traffic", "router-mum-04"),
    "congestion": ("redistribute_traffic", "router-mum-04"),
    "dns-failure": ("restart_dns", "dns-mum-01"),
    "database-latency": ("scale_service", "db-mum-01"),
    "service-crash": ("restart_service", "api-mum-01"),
    "lb-imbalance": ("scale_service", "api-mum-01"),
    "auth-degradation": ("scale_service", "api-mum-01"),
    "complex": None,  # needs two actions (router + database); a single proposal cannot fully resolve it
}


RETRIEVAL_CASES = [
    {"query": "router overload packet loss high latency", "relevant_documents": ["RB-ROUTER-001"]},
    {"query": "DNS resolver timeout high DNS latency", "relevant_documents": ["RB-DNS-001"]},
    {"query": "database query latency connection saturation", "relevant_documents": ["RB-DB-001"]},
    {"query": "service crash failed health checks error rate", "relevant_documents": ["RB-SVC-001"]},
    {"query": "interface saturation throughput utilization queue depth", "relevant_documents": ["RB-CONG-001"]},
    {"query": "edge router CPU saturated redistribute traffic to peers", "relevant_documents": ["RB-ROUTER-001"]},
    {"query": "name resolution failures cascading application errors", "relevant_documents": ["RB-DNS-001"]},
    {"query": "connection pool saturation slow queries", "relevant_documents": ["RB-DB-001"]},
]

_CORE = ["get_network_metrics", "query_logs", "search_runbooks", "search_historical_incidents"]

# (scenario, title, symptoms, ground-truth root cause, expected tools)
SCENARIOS = [
    ("router-overload", "Mumbai router overload", ["high latency", "packet loss", "router saturation"],
     "edge router overload", _CORE + ["get_topology"]),
    ("dns-failure", "Mumbai DNS failure", ["DNS timeout", "high DNS latency", "application errors"],
     "DNS resolver failure", _CORE + ["check_dependencies"]),
    ("database-latency", "Mumbai DB latency", ["database latency", "slow queries", "high API latency"],
     "database performance degradation", _CORE + ["check_dependencies"]),
    ("service-crash", "Mumbai API crash", ["service unavailable", "error spike"],
     "API service crash", ["get_service_health", "query_logs", "search_runbooks", "search_historical_incidents"]),
    ("congestion", "Mumbai network congestion", ["high latency", "packet loss", "throughput saturation"],
     "network congestion", _CORE + ["get_topology"]),
    ("complex", "Mumbai cascading degradation", ["high latency", "packet loss", "database latency"],
     "combined network saturation and database degradation", _CORE + ["check_dependencies", "get_topology"]),
    ("lb-imbalance", "Mumbai load balancer imbalance", ["backend overload", "high latency"],
     "load balancer imbalance", ["get_service_health", "query_logs", "get_topology", "search_runbooks", "search_historical_incidents"]),
    ("auth-degradation", "Mumbai authentication degradation", ["authentication latency", "application errors"],
     "authentication dependency degradation", ["get_service_health", "query_logs", "check_dependencies", "search_runbooks", "search_historical_incidents"]),
]


def lexical_only(db, retriever: HybridRetriever, query: str, k: int = 5):
    rows = db.query(DocumentChunk).all()
    scored = sorted(((retriever._lexical(query, r.text), r) for r in rows), key=lambda x: x[0], reverse=True)[:k]
    return [RetrievalResult(r.id, r.document_id, r.text, r.section, json.loads(r.metadata_json or "{}"), s, lexical_score=s) for s, r in scored]


def dense_only(db, retriever: HybridRetriever, query: str, k: int = 5):
    rows = db.query(DocumentChunk).all()
    qv = retriever.embedder.encode([query])[0]
    scored = sorted(((cosine(qv, json.loads(r.embedding_json or "[]")), r) for r in rows), key=lambda x: x[0], reverse=True)[:k]
    return [RetrievalResult(r.id, r.document_id, r.text, r.section, json.loads(r.metadata_json or "{}"), s, dense_score=s) for s, r in scored]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)], 2)


async def run_async(provider: str | None = None, judge: str | None = None, scenarios: list[str] | None = None) -> dict:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    evaluator = Evaluator()
    # No cross-provider fallback in the benchmark: every number must come from one known agent model.
    agent_llm = LLMClient(provider or settings.llm_provider, fallback="")
    judge_name = (judge or settings.eval_judge_provider or "none").lower()
    # The judge never falls back to another provider, so judged scores always come from one known model.
    judge_llm = None if judge_name in {"none", "demo"} else LLMClient(judge_name, fallback="")
    started_run = time.perf_counter()

    try:
        # ---------------------------------------------------------------- retrieval (deterministic)
        rag = RAGPipeline(db)
        retriever = rag.retriever
        retrieval_functions = {
            "lexical": lambda q: lexical_only(db, retriever, q),
            "dense": lambda q: dense_only(db, retriever, q),
            "hybrid": lambda q: retriever.search(q, top_k=12),
            # Bypass the query cache so latency reflects real retrieval + cross-encoder reranking.
            "hybrid_reranked": lambda q: rag.reranker.rerank(q, retriever.search(q, top_k=12), 5),
        }
        retrieval = {}
        for name, fn in retrieval_functions.items():
            t = time.perf_counter()
            metrics = evaluator.retrieval_metrics(RETRIEVAL_CASES, fn, k=5)
            metrics["latency_ms_per_query"] = round((time.perf_counter() - t) * 1000 / len(RETRIEVAL_CASES), 2)
            retrieval[name] = {k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()}

        # ------------------------------------------------------------- investigations (one per scenario)
        selected = [s for s in SCENARIOS if not scenarios or s[0] in scenarios]
        per_scenario = []
        states = {}
        for scenario, title, symptoms, expected, expected_tools in selected:
            simulator.trigger(scenario)
            inc_id = f"EVAL-{scenario}"
            old = db.get(Incident, inc_id)
            if old:
                db.delete(old)
                db.query(LLMRequest).filter(LLMRequest.incident_id == inc_id).delete()
                db.commit()
            db.add(Incident(id=inc_id, title=title, region="Mumbai", severity="SEV-2", service="API Gateway",
                            symptoms_json=json.dumps(symptoms), ground_truth_root_cause=expected))
            db.commit()
            t = time.perf_counter()
            state = await InvestigationGraph(db, llm=agent_llm).run(inc_id)
            latency_ms = (time.perf_counter() - t) * 1000
            states[scenario] = state
            # Simulated human approval (benchmark only): execute the proposed action and validate recovery.
            action, target = state.remediation.get("action"), state.remediation.get("target")
            execution = build_registry(db).execute(action, approved=True, target=target) if action else {"success": False}
            validation = validate_remediation(target or "")
            expected_fix = EXPECTED_REMEDIATION.get(scenario)
            predicted = canonical_root_cause(state.rca.get("root_cause", ""))
            citations = citation_metrics(state.rca.get("cited_evidence_ids", []), state.evidence)
            per_scenario.append({
                "scenario": scenario,
                "expected_root_cause": expected,
                "predicted_root_cause": state.rca.get("root_cause", ""),
                "correct": predicted == canonical_root_cause(expected),
                "confidence": state.rca.get("confidence", 0.0),
                "rca_method": state.rca.get("method", "unknown"),
                "agent_mode": state.agent_mode,
                "remediation": f"{state.remediation.get('action')} -> {state.remediation.get('target')}",
                "tools": [c["name"] for c in state.tool_calls],
                "expected_tools": expected_tools,
                "remediation_method": state.remediation.get("method"),
                "remediation_correct": None if expected_fix is None else (action, target) == expected_fix,
                "resolved_after_remediation": bool(execution.get("success")) and bool(validation.get("success")),
                "llm_errors": len(state.llm_errors),
                "citations": citations,
                "latency_ms": round(latency_ms, 2),
            })

        case_rows = [{"scenario": s[0], "expected_root_cause": s[3], "expected_tools": s[4]} for s in selected]
        rca = evaluator.rca_metrics(case_rows, lambda c: states[c["scenario"]].rca)
        rca["llm_generated_rca_rate"] = sum(r["rca_method"] == "llm" for r in per_scenario) / max(len(per_scenario), 1)
        llm_only = [r for r in per_scenario if r["rca_method"] == "llm"]
        rca["llm_rca_top1_accuracy"] = (sum(r["correct"] for r in llm_only) / len(llm_only)) if llm_only else None
        agent = evaluator.agent_metrics(case_rows, lambda c: {"tool_calls": states[c["scenario"]].tool_calls, "rca": states[c["scenario"]].rca})
        graded = [r for r in per_scenario if r["remediation_correct"] is not None]
        agent["remediation_accuracy"] = (sum(r["remediation_correct"] for r in graded) / len(graded)) if graded else None
        agent["resolution_rate"] = sum(r["resolved_after_remediation"] for r in per_scenario) / max(len(per_scenario), 1)
        agent["llm_driven_rate"] = sum(r["agent_mode"] == "llm" for r in per_scenario) / max(len(per_scenario), 1)
        agent["scenarios_with_llm_errors"] = sum(r["llm_errors"] > 0 for r in per_scenario)

        # ------------------------------------------------------------- generation
        deterministic_gen = {
            "citation_correctness": round(mean(r["citations"]["citation_correctness"] for r in per_scenario), 4) if per_scenario else 0.0,
            "citation_coverage": round(mean(float(r["citations"]["has_valid_citation"]) for r in per_scenario), 4) if per_scenario else 0.0,
        }
        judged = []
        judge_failures = []
        if judge_llm is not None:
            for row in per_scenario:
                state = states[row["scenario"]]
                answer = f"Root cause: {state.rca.get('root_cause')}. {state.rca.get('summary', '')}"
                score = await evaluator.generation_metrics_llm_judge(
                    judge_llm,
                    {"title": row["scenario"], "region": "Mumbai", "service": "API Gateway", "symptoms": state.incident["symptoms"]},
                    answer,
                    state.rca.get("evidence", []),
                )
                row["judge"] = score
                (judged if score.get("judged") else judge_failures).append(score)
        model_judged = {
            "judge_provider": judge_name,
            "judge_model": judge_llm.providers[0].model if judge_llm else None,
            "judged_cases": len(judged),
            "judge_failures": len(judge_failures),
        }
        for key in ("faithfulness", "hallucination_rate", "answer_relevance"):
            model_judged[key] = round(mean(s[key] for s in judged), 4) if judged else None

        # ------------------------------------------------------------- latency / cost
        lat = [r["latency_ms"] for r in per_scenario]
        llm_rows = db.query(LLMRequest).filter(LLMRequest.incident_id.in_([f"EVAL-{r['scenario']}" for r in per_scenario])).all()
        llm_rows = [r for r in llm_rows if r.provider != "demo"]  # demo mode makes no real model calls
        ok_rows = [r for r in llm_rows if r.status == "success"]
        latency = {
            "investigation_p50_ms": _percentile(lat, 50),
            "investigation_p95_ms": _percentile(lat, 95),
            "investigation_mean_ms": round(mean(lat), 2) if lat else 0.0,
            "llm_calls": len(llm_rows),
            "llm_failed_calls": len(llm_rows) - len(ok_rows),
            "llm_call_mean_ms": round(mean(r.latency_ms for r in ok_rows), 2) if ok_rows else 0.0,
            "llm_input_tokens": sum(r.input_tokens for r in ok_rows),
            "llm_output_tokens": sum(r.output_tokens for r in ok_rows),
            "benchmark_wall_clock_s": round(time.perf_counter() - started_run, 1),
        }

        metrics = {
            "run_config": {
                "agent_provider": agent_llm.provider,
                "agent_model": agent_llm.providers[0].model,
                "judge_provider": judge_name,
                "embedding_model": settings.embedding_model,
                "reranker_model": settings.reranker_model,
                "scenarios": len(per_scenario),
                "retrieval_queries": len(RETRIEVAL_CASES),
            },
            "retrieval": retrieval,
            "rca": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in rca.items()},
            "agent": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in agent.items()},
            "generation": {"deterministic": deterministic_gen, "model_judged": model_judged},
            "latency": latency,
            "per_scenario": per_scenario,
            "methodology": {
                "deterministic": ["retrieval (precision/recall/hit_rate/mrr/ndcg vs labelled runbooks)",
                                  "rca accuracy vs simulator ground truth", "tool selection/recall/unnecessary-tool rate vs expected tools",
                                  "remediation accuracy vs expected action (where one is unambiguous)",
                                  "resolution rate: proposed action executed (simulated approval) and validated against simulator telemetry",
                                  "citation correctness (cited ids exist in gathered evidence)", "latency and token counts"],
                "model_judged": ["faithfulness", "hallucination_rate = 1 - faithfulness", "answer_relevance"],
                "notes": "Scores for unjudged cases are omitted, never imputed. rca_method=rule_based_fallback marks RCAs produced without the LLM.",
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _log_mlflow(metrics)
        db.add(EvaluationRun(name=f"nocmind-benchmark ({agent_llm.provider}:{agent_llm.providers[0].model})", metrics_json=json.dumps(metrics, default=str)))
        db.commit()
        return metrics
    finally:
        simulator.reset()
        db.close()


def _log_mlflow(metrics: dict) -> None:
    try:
        import mlflow
        mlflow.set_experiment("nocmind")
        with mlflow.start_run(run_name="benchmark"):
            mlflow.log_params({k: str(v) for k, v in metrics["run_config"].items()})
            flat = {f"retrieval_{variant}_{k}": v for variant, vals in metrics["retrieval"].items() for k, v in vals.items()}
            for group in ("rca", "agent", "latency"):
                flat.update({f"{group}_{k}": v for k, v in metrics[group].items()})
            for sub in ("deterministic", "model_judged"):
                flat.update({f"generation_{sub}_{k}": v for k, v in metrics["generation"][sub].items()})
            for key, value in flat.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    mlflow.log_metric(key.replace("@", "_at_"), value)
    except Exception:
        pass


def run(provider: str | None = None, judge: str | None = None, scenarios: list[str] | None = None) -> dict:
    """Synchronous entrypoint for CLI callers. Use run_async() from an event loop."""
    return asyncio.run(run_async(provider, judge, scenarios))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the NOCMind benchmark")
    parser.add_argument("--provider", help="agent LLM provider (default: LLM_PROVIDER)")
    parser.add_argument("--judge", help="judge provider: ollama | openrouter | none (default: EVAL_JUDGE_PROVIDER)")
    parser.add_argument("--scenarios", help="comma-separated scenario subset")
    args = parser.parse_args()
    result = run(args.provider, args.judge, args.scenarios.split(",") if args.scenarios else None)
    print(json.dumps(result, indent=2, default=str))

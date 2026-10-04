# Evaluation

`python -m app.evaluation.run_benchmark` (or **Run benchmark** in the UI, or `make evaluate`) runs one pass over
8 labelled retrieval queries and 8 simulator scenarios. It stores the result as an `EvaluationRun` and logs it to MLflow.

* Each scenario is investigated **once** by the real agent, and each RCA is judged **once**: about 50 LLM calls in total.
* The benchmark agent never falls back to another provider, and the judge never falls back either, so every number comes from known models.
* With `--provider ollama --judge ollama` (the Compose defaults) the run needs **no external API or quota**.
  `--judge none` disables the model judge, leaving only deterministic metrics.

## Deterministic metrics (ground truth)

| Group | Metric | Definition |
|---|---|---|
| Retrieval (lexical / dense / hybrid / hybrid+rerank) | precision@5, recall@5, hit rate, MRR, nDCG | against the labelled relevant runbook per query |
| RCA | top-1 / top-3 accuracy, false-diagnosis rate, abstention rate, mean confidence | predicted root cause (alias-normalised) vs simulator ground truth |
| RCA | `llm_generated_rca_rate`, `llm_rca_top1_accuracy` | share of RCAs produced by the LLM, and accuracy on those alone. Rule-based fallbacks are labelled, never counted as model output |
| Agent | tool selection accuracy, tool recall, unnecessary-tool rate, avg tool calls, completion rate | tools the model actually called vs each scenario's expected tool set |
| Agent | remediation accuracy | proposed (action, target) vs the expected fix, for the 5 scenarios with one unambiguous fix |
| Agent | resolution rate | proposed action executed (simulated approval) and validated against simulator telemetry |
| Generation | citation correctness, citation coverage | ids cited by the RCA that exist in the evidence the agent actually gathered |
| Latency / cost | investigation p50/p95/mean, LLM calls, failed calls, mean LLM call latency, input/output tokens | measured; tokens as reported by the provider |

## Model-judged metrics (LLM-as-judge)

| Metric | Definition |
|---|---|
| faithfulness | judge score 0–1: are the RCA's claims supported by the cited evidence |
| hallucination rate | `1 − faithfulness` |
| answer relevance | judge score 0–1: does the answer address the incident usefully |

If the judge fails for a case, that case is counted in `judge_failures` and **left out of the averages**; heuristic
numbers are never substituted. The default judge is the same local model as the agent (Qwen3 4B), which is a known
limitation: a small model judging its own outputs is lenient and noisy. Use `EVAL_JUDGE_PROVIDER=openrouter` for a
stronger external judge when quota allows.

## Caveats
* 8 scenarios and 8 queries is a small, synthetic benchmark. The numbers show the system works end to end. They are not statistical estimates.
* Retrieval runs over a small corpus (5 runbooks + 5 historical incidents), which is why every variant has a near-perfect hit rate.
* LLM output varies from run to run, even at temperature 0.1.
* Results for one run are recorded in `docs/verification.md`.

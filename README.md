# Crucible

An eval and red-team harness for agents. It runs a target against versioned datasets, scores trajectories as well as answers, attacks the target on purpose, and fails a pull request when quality regresses.

> **Status: phase 0.** The golden dataset exists and a runner is being stubbed out. Everything under [Roadmap](#roadmap) beyond phase 0 is planned, not built. This README says so deliberately, and will be updated as phases ship.

## Why

Agent evals are not LLM evals. A model eval asks whether an output is good. An agent eval asks whether it picked the right tools, in a sensible order, recovered from failures, stopped when it had enough, and stayed in budget. A correct answer reached through twelve redundant searches and a lucky guess is a bad result, and scoring only the answer will optimise you straight into it.

Crucible is built around the problems that make this hard:

- **Non-determinism.** One run tells you nothing. Every case runs *n* times; every metric is reported as mean with a bootstrap 95% CI, never a bare number.
- **No ground truth for trajectories.** There is rarely one correct tool sequence, so scoring uses rubrics and reference-free checks rather than exact matching.
- **Judges are models too.** LLM judges have position, verbosity and self-preference bias. Judges are validated against human labels before their numbers are trusted.
- **Multi-objective.** Quality, cost and latency trade off. Cost and latency are gate conditions, not footnotes.
- **Adversarial behaviour is invisible in normal use.** The agent has to be attacked deliberately.

## Current state

```
datasets/golden.jsonl   40 question/answer cases over NIST documents (Atlas golden set)
main.py                 scratch runner: loads the dataset, no scoring yet
```

Each golden case looks like:

```json
{"id": "q001", "question": "...", "answer": "...", "doc": "AI Risk Management.pdf", "page": 6, "type": "single_hop"}
```

First target is [Atlas](#how-it-fits-in-the-wider-stack) (a RAG service), reached over HTTP.

### Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/) (a `.venv` is already present in the repo).

```bash
uv sync
python main.py
```

## Architecture (target)

```
 datasets/                         targets/
  golden.jsonl (versioned)          - HTTP endpoint
  redteam.jsonl                     - python callable
  trajectories/ (recorded)          - CLI subprocess
        |                                |
        +----------------+---------------+
                         v
                    [ RUNNER ]
        n repeats x m cases, async, OpenTelemetry on every call
                         |
                         v
                  [ TRACE STORE ]   spans: llm, tool, retriever, agent
                         |
        +----------------+----------------+
        v                v                v
 [ deterministic ]  [ llm judges ]   [ trajectory ]
  exact match        rubric-scored    tool precision/recall
  regex / schema     pairwise, with   redundancy, recovery
  citation valid     calibration      budget adherence
  cost & latency
        |                |                |
        +----------------+----------------+
                         v
                  [ AGGREGATOR ]
        mean + bootstrap 95% CI, segmented by type and difficulty
                         |
        +----------------+----------------+
        v                                 v
   [ CI GATE ]                       [ REPORT ]
   compare vs baseline,              run diff, per-case drilldown,
   fail on regression,               trace viewer, cost/quality
   comment on the PR                 scatter
```

## Planned stack

| Layer | Choice |
|---|---|
| Core | Python, asyncio, a pytest plugin (evals that run as tests get run) |
| Tracing | OpenTelemetry + OpenInference conventions, exported to self-hosted Langfuse |
| Results | Postgres for runs, DuckDB over Parquet for analysis |
| Judges | A strong model from a *different family* than the one under test |
| Stats | scipy, bootstrap resampling |
| CI | GitHub Actions: cheap subset per PR, full suite nightly |
| Report UI | Next.js |

Metric definitions are borrowed from Ragas, DeepEval, promptfoo and garak where they fit, so numbers stay comparable to the rest of the field.

## Metrics

**Trajectory**

| Metric | Definition | Catches |
|---|---|---|
| Tool selection precision | necessary tools called ÷ tools called | shotgunning every tool |
| Tool selection recall | necessary tools called ÷ necessary tools | skipping a required check |
| Redundancy | calls duplicating an earlier call's arguments | loops, rephrasing |
| Recovery rate | of runs where a tool errored, share that still succeeded | brittleness |
| Step efficiency | steps taken ÷ steps in reference solution | wandering |
| Stop accuracy | stopped when it had enough vs. kept going / quit early | premature answers, endless exploration |
| Budget adherence | share of runs inside token and step budget | cost blowouts |

**Variance** is a first-class metric: mean *and* standard deviation over repeats. A change that raises the mean 2 points while doubling the spread is worse, and a single-run comparison would call it better. High-variance cases are flagged for fixing or quarantine.

**Regression rule:** a metric regresses only if the confidence intervals separate *and* the gap exceeds a minimum effect size. Gates are checked per segment as well as in aggregate, plus cost and latency guards.

## Judges

An LLM judge is an unvalidated instrument until proven otherwise:

1. Hand-label 50 examples on the same rubric.
2. Run the judge on the same 50.
3. Compute Cohen's kappa (categorical) or Spearman (ordinal). Below ~0.6, the judge is measuring something other than intended.
4. Fix the rubric (concrete anchors), not the model.
5. Re-validate whenever the rubric or judge model changes; pin the judge model version in every run record.

Pairwise comparisons randomise A/B order and run each pair twice with the order swapped, discarding pairs where the verdict flips. Results will be published in `docs/judge-calibration.md`.

## Adversarial suite (planned)

Attack success rate is reported **per category**, never as one aggregate, and tracked over time.

| Category | Pass condition |
|---|---|
| Direct injection | Refuses, keeps original constraints |
| Indirect injection (in retrieved docs, web pages, tool results) | Treats it as data, not instruction |
| Tool abuse | Refuses, or the call is blocked by policy |
| Exfiltration | Secret never leaves; ideally two independent controls |
| Context overflow | Still follows the real instruction under 50k tokens of filler |
| Multi-turn escalation | Refuses at the point the premise turns |
| Loop induction | Governor aborts within budget |
| Schema abuse | Path traversal / SQL / shell metacharacters rejected before execution |

Residual rates will be stated plainly. Nobody blocks all indirect injection today, and "not zero, and we say so" is the intended posture.

## Roadmap

| Phase | Goal | Ship |
|---|---|---|
| 0 | One dataset, one metric, one target | `crucible run --dataset golden --target atlas` printing an exact-match number **← here** |
| 1 | Repeats and bootstrap CIs | every metric reported as mean ± CI |
| 2 | OpenTelemetry tracing, trajectory metrics | trajectory scorecard next to answer quality |
| 3 | Calibrated judges | `docs/judge-calibration.md` with kappa scores |
| 4 | CI gate | a PR that fails on a deliberately-worse prompt |
| 5 | Adversarial suite | per-category red-team scorecards |
| 6 | Report UI | public run-diff and drilldown page |

## Evaluating the evaluator

The harness has its own quality bar:

- **Judge agreement:** kappa > 0.6 against human labels, measured and published.
- **Discriminative power:** deliberately-worse variants (truncated prompt, disabled reranker, shuffled tool list) must all be detected.
- **Stability:** two runs of an identical config must fall within each other's CIs, otherwise *n* is too small.
- **Speed:** the PR-tier suite finishes in under ten minutes, or people will bypass the gate.

## Known failure modes to design against

Goodhart (tuning to the eval set; keep a held-out set looked at only at release), judge drift (pin versions, re-baseline on change), single-number thinking (always segment), cost blindness, flaky cases treated as signal, and an eval set that never grows (every production bug becomes a case).

## How it fits in the wider stack

Crucible is one of seven layers in the AI Systems Build Lab: Recall (memory), Atlas (RAG), Orchestra (agent runtime), Cellar (sandbox), Aegis (MCP security), Sentinel (SOC triage), and Crucible, which scores all of them with one dataset format, one scorecard and one CI gate.

## Reference reading

[Langfuse](https://github.com/langfuse/langfuse), [Phoenix / OpenInference](https://github.com/Arize-ai/phoenix), [Ragas](https://github.com/explodinggradients/ragas), [DeepEval](https://github.com/confident-ai/deepeval), [promptfoo](https://github.com/promptfoo/promptfoo), [garak](https://github.com/NVIDIA/garak), [MLflow](https://github.com/mlflow/mlflow).

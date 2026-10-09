# Phase 1 findings

Target: Atlas (`/api/v1/query`), dataset `datasets/golden.jsonl` (30 cases: 20 single_hop, 5 multi_hop, 5 table_lookup).
Every interval is a 95% bootstrap CI over cases (repeats are averaged within a case first). Source runs are in `runs/`.

## Baseline numbers

`run_20261009-151232_baseline-stability-a`: 30 cases × 2 repeats, 2 warm-up queries, concurrency 1, 0 errors.
Atlas config recorded in the run: `retrieval_mode=hybrid_graph`, `rerank_enabled=false`, `graph_enabled=false`, `agentic_enabled=false`, `final_k=8`, `candidate_k=40`.

| metric | mean [95% CI] | within-case std |
| :-- | :-- | :-- |
| hit@5 | 0.90 [0.80, 1.00] | 0.000 |
| hit@all | 0.97 [0.90, 1.00] | 0.000 |
| answer_ok | 0.12 [0.02, 0.23] | 0.017 |
| latency (s) | 12.0 [10.7, 13.3] | 6.9 |
| tokens | 3268 [2947, 3563] | 2.6 |

By case type (`crucible report` prints this for every run):

| type | cases | hit@5 | answer_ok |
| :-- | :-- | :-- | :-- |
| single_hop | 20 | 0.90 [0.75, 1.00] | 0.15 [0.00, 0.35] |
| multi_hop | 5 | 1.00 [1.00, 1.00] | **0.00 [0.00, 0.00]** |
| table_lookup | 5 | 0.80 [0.40, 1.00] | 0.10 [0.00, 0.30] |

multi_hop retrieves the right page every time but never produces an answer that passes the matcher, so its failures happen in answer synthesis (or scoring), not retrieval. With 5 cases per segment the intervals are wide. The dataset has no `difficulty` field, so there is no difficulty segment yet.

Repeats were reduced from 5 to 2 to keep run time down.
`answer_ok` is a substring check, so treat it as a lower bound (see the README warning).
Latency is mostly a measure of Ollama's prompt cache (see the stability check below).

## Flaky cases

**1 case** is flaky on answer_ok: **q039** (table_lookup). It passed 1 of 2 repeats in both `hybrid-graph-r2` and `stability-a`, and 2 of 2 in `stability-b`. Nothing is flaky on hit@5.

- **Cause:** generation nondeterminism, not retrieval. Every repeat retrieved the same chunks. The expected tag list is `Data Privacy; Harmful Bias and Homogenization; Intellectual Property`, and the failing repeats drop "Harmful Bias and Homogenization".
- **Status:** noted, not fixed. This is a real model miss, so the scorer is right to fail it.

**Hidden variance.** The answer text differs between repeats in **9/30 cases** (q005, q006, q007, q010, q021, q024, q025, q039, q040), but answer_ok flips in only one of them. Most of those answers fail the strict matcher both times, so the binary score hides most of the generation variance. A looser matcher or a judge will probably surface more flaky cases.

## Stability check

`baseline-stability-a` and `baseline-stability-b` used the same recorded config and ran back to back, each after 2 warm-up queries:

| metric | a | b | verdict |
| :-- | :-- | :-- | :-- |
| hit@5 | 0.90 [0.80, 1.00] | 0.90 [0.80, 1.00] | no detectable change |
| hit@all | 0.97 [0.90, 1.00] | 0.97 [0.90, 1.00] | no detectable change |
| answer_ok | 0.12 [0.02, 0.23] | 0.13 [0.03, 0.27] | no detectable change |
| tokens | 3268 [2957, 3563] | 3268 [2956, 3564] | no detectable change |
| latency (s) | 12.0 [10.6, 13.3] | 5.13 [4.55, 5.72] | **IMPROVED (−6.85 [−8.00, −5.68])** |

**Result:** quality and cost are stable, so the stability check passes for Phase 1. **Latency is excluded from Phase 1 and deferred** because it is not stable, and the cause is confirmed: **Ollama's prompt cache**. When Ollama gets a prompt it has already processed, it reuses that work instead of recomputing it (the Ollama log shows `found better prompt ... sim = 1.000`):

| attempt | n | mean latency |
| :-- | :-- | :-- |
| run a, first time a case's prompt is sent | 30 | 18.8 s |
| run a, second time (prompt already seen) | 30 | 5.2 s |
| run b, every prompt already seen in run a | 60 | 5.1 s |

The 2 warm-up queries cover only 2 of the 30 prompts, so they can't fix this. With the current setup, any run that follows another run on the same dataset will look faster. **Latency verdicts from `compare` are not trustworthy** until one of these is done:

- turn off prompt caching for eval runs (an Atlas/Ollama setting), or
- warm up with the full dataset, so every timed attempt is a cache hit, or
- report cold latency (first attempt per case) and warm latency separately.

## Old claims

| claim (source) | verdict | evidence |
| :-- | :-- | :-- |
| The graph adds no measurable end-to-end gain over hybrid (Atlas README, 2026-09-28) | **held up** | hybrid vs hybrid_graph: no change on any quality metric. Retrieved chunks are identical in 30/30 cases, so the graph is not affecting retrieval at all, consistent with `graph_enabled=false` in the recorded config. |
| Retrieval is not the bottleneck (Atlas README) | **held up** | hit@all 0.97 vs answer_ok 0.12. Part of that gap is the strict matcher, not the model. |
| Hybrid search costs more tokens than baseline | **held up** | +302 [+171, +439] tokens per query, consistent across every run |
| hybrid-graph is slower than baseline (2026-10-08 run, +4.2 s) | **didn't hold up** | the same hybrid_graph config later ran 9.4 s faster; latency depends on prompt-cache state, not on the config |
| baseline-repeat was faster than baseline (2026-10-04, −13.3 s) | **didn't hold up** | identical config; run b reused run a's cached prompts |
| A couple of warm-up queries make latency comparable (phase 1 fix) | **didn't hold up** | with warm-up, identical runs still differ by 6.9 s, because the cache works per prompt, not per model |
| One run per config is enough to compare (phase 0 practice) | **partly held up** | fine for retrieval (deterministic) and tokens; wrong for latency and for answer text, which varies in 9/30 cases |

## Phase 1 checklist

- [x] Smoke test and resume work
- [x] Baseline run with repeats (2 instead of 5)
- [x] Stability check showing "no detectable change" on hit@5, hit@all, answer_ok and tokens. Latency is excluded for now (see below).
- [x] Every flaky case fixed, explained, or noted (q039, under the current matcher)
- [x] This file, including old conclusions that didn't survive

## Deferred: latency

Latency is still recorded and printed, but it is **not part of the Phase 1 stability criterion**, and its verdicts in `compare` shouldn't be trusted yet. The planned fix is to restart Ollama before each run, warm up with a prompt that isn't in the dataset, and report `latency_cold` (first attempt per case) separately from `latency_warm` (repeat attempts).

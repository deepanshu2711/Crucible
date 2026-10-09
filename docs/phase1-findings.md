# Phase 1 findings

Target: Atlas (`/api/v1/query`), dataset `datasets/golden.jsonl` (30 cases: 20 single_hop, 5 multi_hop, 5 table_lookup).
Every interval is a 95% bootstrap CI over cases (repeats are averaged within a case first). Source runs are in `runs/`.

## Baseline numbers

The 5-repeat baseline has **not been run yet**. The best data so far is `run_20261009-145244_hybrid-graph-r2` (Atlas `retrieval_mode=hybrid_graph`, agentic off, 30 cases × 2 repeats, 0 errors):

| metric | mean [95% CI] | within-case std |
| :-- | :-- | :-- |
| hit@5 | 0.90 [0.77, 1.00] | 0.000 |
| hit@all | 0.97 [0.90, 1.00] | 0.000 |
| answer_ok | 0.12 [0.02, 0.23] | 0.017 |
| latency (s) | 12.1 [10.7, 13.5] | 6.7 |
| tokens | 3268 [2957, 3571] | 2.6 |

`answer_ok` is a substring check, so treat it as a lower bound (see the README warning).

## Flaky cases

**1 case** is flaky on answer_ok: **q039** (table_lookup), which passed 1 of 2 repeats. Nothing is flaky on hit@5.

- **Cause:** generation nondeterminism, not retrieval. Both repeats retrieved the same chunks. The expected tag list is `Data Privacy; Harmful Bias and Homogenization; Intellectual Property`. Repeat 1 listed all three, and repeat 0 dropped "Harmful Bias and Homogenization".
- **Status:** noted, not fixed. This is a real model miss, so the scorer is right to fail it.

**Hidden variance.** The answer text differs between repeats in **9/30 cases** (q005, q006, q007, q010, q021, q024, q025, q039, q040), but answer_ok flips in only one of them. Most of those answers fail the strict matcher both times, so the binary score hides most of the generation variance. A looser matcher or a judge will probably surface more flaky cases.

## Stability check

Two pairs of runs used the same Atlas config:

| pair | hit@5 | hit@all | answer_ok | tokens | latency |
| :-- | :-- | :-- | :-- | :-- | :-- |
| baseline vs baseline-repeat | no change | no change | no change | no change | **17.3 → 4.1 s, CIs don't overlap** |
| hybrid-graph vs hybrid-graph-r2 | no change | no change | no change | no change | **21.5 → 12.1 s, CIs don't overlap** |

**Result:** the check passes for quality and cost but **fails for latency**. Retrieval is deterministic: the retrieved chunks match in 30/30 cases in both pairs. Latency moves 9–13 s between identical runs. Atlas isn't caching responses, because answer text changed in 6–9 of 30 cases while latency dropped. The likely cause is server or model state (cold vs warm model, other load on the machine), but that is unconfirmed.

Until the latency setup is controlled (a warm-up query before each run, baseline and candidate run back to back), **latency verdicts from `compare` are not trustworthy.**

## Old claims

| claim (source) | verdict | evidence |
| :-- | :-- | :-- |
| The graph adds no measurable end-to-end gain over hybrid (Atlas README, 2026-09-28) | **held up** | hybrid vs hybrid_graph: no change on any quality metric. Retrieved chunks are identical in 30/30 cases, so the graph is not affecting retrieval at all. Worth checking whether `graph_enabled` (default `False` in Atlas `config.py`) is actually on. |
| Retrieval is not the bottleneck (Atlas README) | **held up** | hit@all 0.97 vs answer_ok 0.12. Part of that gap is the strict matcher, not the model. |
| Hybrid search costs more tokens than baseline | **held up** | +302 [+171, +439] tokens per query, consistent across every run |
| hybrid-graph is slower than baseline (2026-10-08 run, +4.2 s) | **didn't hold up** | the same hybrid_graph config later ran 9.4 s faster, so this difference is within run-to-run noise |
| baseline-repeat was faster than baseline (2026-10-04, −13.3 s) | **didn't hold up** | identical config; the difference is server state, not a change |
| One run per config is enough to compare (phase 0 practice) | **partly held up** | fine for retrieval (deterministic) and tokens; wrong for latency and for answer text, which varies in 9/30 cases |

## Gaps before Phase 1 is done

- [x] Smoke test and resume work
- [ ] Baseline run with 5 repeats
- [ ] Stability check showing "no detectable change" everywhere: currently fails on latency
- [x] Every flaky case fixed, explained, or noted (q039, under the current matcher)
- [x] This file, including old conclusions that didn't survive

The run config also doesn't record Atlas's own settings (retrieval mode, graph, agentic), so older runs such as "baseline" can't be tied to a specific config. That should be recorded before the 5-repeat baseline is run.

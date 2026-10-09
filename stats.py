"""
Crucible stats — Phase 1.

Everything here works on a list of attempt dicts (one per line of a run
file) and a metric function that turns one attempt into a number
(or None if that attempt has no value for the metric).
"""

from collections import defaultdict

import numpy as np


def per_case(attempts, fn):
    """Group one metric by case: {case_id: [value_repeat0, value_repeat1, ...]}"""
    by_case = defaultdict(list)
    for a in attempts:
        v = fn(a)
        if v is not None:
            by_case[a["id"]].append(float(v))
    return dict(by_case)


def bootstrap_ci(values, n_boot=2000, alpha=0.05, seed=0):
    """Mean and 95% CI of `values` by resampling them with replacement.
    `values` must be independent units — here, one number per CASE."""
    v = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)  # fixed seed: same data, same CI
    idx = rng.integers(0, len(v), size=(n_boot, len(v)))
    boots = v[idx].mean(axis=1)
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(v.mean()), float(lo), float(hi)


def summarize(attempts, fn):
    """Case-level bootstrap: average the repeats within each case first,
    then bootstrap over cases."""
    pc = per_case(attempts, fn)
    if not pc:
        return None
    case_means = [np.mean(v) for v in pc.values()]
    mean, lo, hi = bootstrap_ci(case_means)
    stds = [np.std(v) for v in pc.values() if len(v) > 1]
    return {
        "mean": mean,
        "lo": lo,
        "hi": hi,
        "n_cases": len(pc),
        "n_attempts": sum(len(v) for v in pc.values()),
        # average run-to-run spread inside a case; NaN when repeats == 1
        "within_std": float(np.mean(stds)) if stds else float("nan"),
    }


def paired_diff(attempts_a, attempts_b, fn):
    """Candidate minus baseline, computed per case, then bootstrapped.
    Pairing removes 'some questions are just hard' from the noise."""
    a = {k: np.mean(v) for k, v in per_case(attempts_a, fn).items()}
    b = {k: np.mean(v) for k, v in per_case(attempts_b, fn).items()}
    common = sorted(set(a) & set(b))
    if not common:
        return None
    diffs = [b[k] - a[k] for k in common]
    mean, lo, hi = bootstrap_ci(diffs)
    base = bootstrap_ci([a[k] for k in common])
    cand = bootstrap_ci([b[k] for k in common])
    return {
        "mean": mean,
        "lo": lo,
        "hi": hi,
        "n": len(common),
        "base_mean": base[0],
        "cand_mean": cand[0],
        "base_ci": base,
        "cand_ci": cand,
    }


def flaky_cases(attempts, fn):
    """Cases where a 0/1 metric passed on some repeats and failed on others."""
    out = []
    for cid, vals in sorted(per_case(attempts, fn).items()):
        passes = int(sum(vals))
        if 0 < passes < len(vals):
            out.append((cid, passes, len(vals)))
    return out


def fmt(mean, lo, hi, signed=False):
    """The only way numbers get printed: always with their interval."""
    big = max(abs(mean), abs(lo), abs(hi))
    p = 2 if big < 10 else 1 if big < 1000 else 0
    s = "+" if signed else ""
    return f"{mean:{s}.{p}f} [{lo:{s}.{p}f}, {hi:{s}.{p}f}]"

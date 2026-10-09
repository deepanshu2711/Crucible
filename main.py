import json
import os
import time
import requests
import argparse
import re
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
import stats

TARGETS = {"atlas": "http://localhost:8080/api/v1/query"}
RUNS_DIR = "runs"
TIMEOUT_S = 600


def load_cases(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def ask_atlas(case, url):
    body = {"document_id": case["document_id"], "query": case['question']}
    resp = requests.post(url, json=body, timeout=TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def first_relevant_rank(sources, relevant_pages):
    for rank, src in enumerate(sources, start=1):
        if set(src["metadata"].get("pages", [])) & set(relevant_pages):
            return rank
    return None


def answer_matches(answer, expected):
    return expected.lower() in answer.lower()


def score(case, out):
    sources = out.get("sources", [])
    trace = out.get("trace") or {}

    return {
        "id": case["id"],
        "rank": first_relevant_rank(sources, case["page"]),
        "answer_ok": answer_matches(out.get("answer", ""), case["answer"]),
        "answer": out.get("answer"),
        "retrieved": [
            {"chunk": s["metadata"].get(
                "chunk_index"), "pages": s["metadata"].get("pages")}
            for s in sources
        ],
        "latency_s": trace.get("latency_s"),
        "tokens": (trace.get("input_tokens") or 0) + (trace.get("output_tokens") or 0),
        "llm_calls": trace.get("llm_calls"),
        "mode": trace.get("mode"),
    }


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "no-label"


def load_run(path):
    config, attempts = None, []
    with open(path) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = row.get("type")
            if kind == "config":
                config = row
            elif kind == "case":
                row.setdefault("repeat_idx", 0)
                row.setdefault("error", None)
                attempts.append(row)

    return config, attempts


def run_one(case, repeat_idx, url):
    row = {"id": case["id"], "repeat_idx": repeat_idx}
    t0 = time.time()
    try:
        row.update(score(case, ask_atlas(case, url)))
        row["error"] = None
    except Exception as e:
        row["error"] = f"{type(e).__name__}:{e}"
    row["wall_s"] = round(time.time() - t0, 2)
    return row


def parse_target_config(pairs):
    """--target-config retrieval_mode=hybrid_graph -> {"retrieval_mode": "hybrid_graph"}"""
    out = {}
    for p in pairs or []:
        key, sep, val = p.partition("=")
        if not sep or not key:
            raise SystemExit(f"--target-config expects key=value, got {p!r}")
        out[key.strip()] = val.strip()
    return out


def warm_up(cases, url, n):
    """Send n untimed queries first so a cold model doesn't inflate latency."""
    for c in cases[:n]:
        t0 = time.time()
        try:
            ask_atlas(c, url)
            print(f"warm-up {c['id']}  {time.time() - t0:.1f}s")
        except Exception as e:
            print(f"warm-up {c['id']}  failed: {type(e).__name__}: {e}")


def cmd_run(args):
    if args.resume:
        run_path = args.resume
        config, attempts = load_run(run_path)
        if config is None:
            raise SystemExit(f"{run_path} has no config line; can't resume it")
        done = {(a["id"], a["repeat_idx"])
                for a in attempts if a['error'] is None}
        cases = load_cases(config['dataset'])
    else:
        dataset = f"datasets/{args.dataset}.jsonl"
        cases = load_cases(dataset)
        config = {
            "type": "config",
            "label": args.label,
            "target": args.target,
            "atlas_url": TARGETS[args.target],
            "dataset": dataset,
            "cases": len(cases),
            "repeats": args.repeats,
            "concurrency": args.concurrency,
            "warmup": args.warmup,
            "target_config": parse_target_config(args.target_config),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        os.makedirs(RUNS_DIR, exist_ok=True)
        run_path = os.path.join(
            RUNS_DIR, f"run_{time.strftime('%Y%m%d-%H%M%S')}_{slug(args.label)}.jsonl")

        with open(run_path, "w") as f:
            f.write(json.dumps(config) + "\n")
        done = set()

    jobs = [
        (c, r)
        for c in cases
        for r in range(config["repeats"])
        if (c["id"], r) not in done
    ]

    if jobs:
        warm_up(cases, config["atlas_url"], args.warmup)
    random.shuffle(jobs)
    print(f"{run_path}\n{len(cases)} cases x {config['repeats']} repeats, "
          f"{len(done)} done, {len(jobs)} to go\n")
    pool = ThreadPoolExecutor(max_workers=config.get("concurrency", 1))
    futures = [
        pool.submit(run_one, c, r, config["atlas_url"])
        for c, r in jobs
    ]
    try:
        with open(run_path, "a") as f:
            for i, fut in enumerate(as_completed(futures), 1):
                r = fut.result()
                f.write(json.dumps({"type": "case", **r}) + "\n")
                f.flush()

                if r["error"]:
                    status = f"ERROR {r['error'][:70]}"
                else:
                    rank = r["rank"] if r["rank"] is not None else "-"
                    ok = "OK   " if r["answer_ok"] else "wrong"
                    status = f"rank {rank:>2}  {ok}"
                print(f"[{i:>3}/{len(jobs)}] {r['id']} r{r['repeat_idx']}  {status}")
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        print(f"\nInterrupted. Resume with:\n  crucible run --resume {run_path}")
        raise SystemExit(1)
    pool.shutdown()
    print_report(run_path)


METRICS = {
    "hit@5":     (lambda r: float(r["rank"] is not None and r["rank"] <= 5), "higher"),
    "hit@all":   (lambda r: float(r["rank"] is not None), "higher"),
    "answer_ok": (lambda r: float(r["answer_ok"]), "higher"),
    "latency_s": (lambda r: r.get("latency_s"), "lower"),
    "tokens":    (lambda r: r.get("tokens"), "lower"),
}
BINARY = {"hit@5", "hit@all", "answer_ok"}


def print_report(path):
    config, attempts = load_run(path=path)
    ok = [a for a in attempts if a['error'] is None]
    n_err = len(attempts) - len(ok)
    label = (config or {}).get("label", "?")
    print(f"\n{os.path.basename(path)}   label: {label}")

    if not attempts:
        print("no attempts")
        return
    print(f"attempts {len(attempts)}   ok {len(ok)}   "
          f"errors {n_err} ({n_err / len(attempts):.0%})")

    if not ok:
        return

    print(f"\n{'metric':<11} {'mean [95% CI]':<26} {
          'cases x reps':<14} within-case std")

    for name, (fn, _) in METRICS.items():
        s = stats.summarize(ok, fn=fn)
        if s is None:
            continue
        reps = s["n_attempts"] / s["n_cases"]
        wstd = "-" if s["within_std"] != s["within_std"] else f"{
            s['within_std']:.3f}"
        print(f"{name:<11} {stats.fmt(s['mean'], s['lo'], s['hi']):<26} "
              f"{s['n_cases']} x {reps:<9.1f} {wstd}")

    n_cases = len({a["id"] for a in ok})
    if n_cases < 30:
        print(f"\nnote: only {
              n_cases} cases — intervals will be wide and somewhat unreliable")

    for name in ("answer_ok", "hit@5"):
        flaky = stats.flaky_cases(ok, METRICS[name][0])
        if flaky:
            print(f"\nflaky on {name} ({len(flaky)} cases):")
            for cid, passes, total in flaky:
                print(f"  {cid:<12} {passes}/{total}")


def cmd_report(args):
    print_report(args.run)


def verdict(d, better, binary, min_effect, min_rel):
    if d["lo"] <= 0 <= d["hi"]:
        return "no detectable change"
    if binary:
        size, threshold = abs(d["mean"]), min_effect
    else:
        size = abs(d["mean"]) / abs(d["base_mean"]
                                    ) if d["base_mean"] else float("inf")
        threshold = min_rel
    if size < threshold:
        return "real but too small to matter"
    went_up = d["mean"] > 0
    good = went_up if better == "higher" else not went_up
    return "IMPROVED" if good else "REGRESSED"


def cmd_compare(args):
    cfg_a, att_a = load_run(args.baseline)
    cfg_b, att_b = load_run(args.candidate)
    if cfg_a and cfg_b and cfg_a.get("dataset") != cfg_b.get("dataset"):
        print("WARNING: runs used different datasets; only shared case ids are compared")

    ok_a = [a for a in att_a if a["error"] is None]
    ok_b = [a for a in att_b if a["error"] is None]
    la = (cfg_a or {}).get("label", "?")
    lb = (cfg_b or {}).get("label", "?")
    print(f"baseline  {os.path.basename(args.baseline)}  ({la})")
    print(f"candidate {os.path.basename(args.candidate)}  ({lb})")
    tc_a = (cfg_a or {}).get("target_config")
    tc_b = (cfg_b or {}).get("target_config")
    if tc_a or tc_b:
        print(f"  base config: {tc_a or 'not recorded'}")
        print(f"  cand config: {tc_b or 'not recorded'}")
    print(f"\n{'metric':<11} {'base [95% CI]':<22} {'cand [95% CI]':<22} "
          f"{'diff [95% CI]':<26} {'cases':>5}  verdict")

    for name, (fn, better) in METRICS.items():
        d = stats.paired_diff(ok_a, ok_b, fn)
        if d is None:
            continue
        v = verdict(d, better, name in BINARY, args.min_effect, args.min_rel)
        print(f"{name:<11} {stats.fmt(*d['base_ci']):<22} {stats.fmt(*d['cand_ci']):<22} "
              f"{stats.fmt(d['mean'], d['lo'], d['hi'], signed=True):<26} "
              f"{d['n']:>5}  {v}")


def parse_args():
    p = argparse.ArgumentParser(prog="crucible")

    # NOTE: this create a system for multiple subcommands inside cli dest="cmd" tell argparse to store the selected command in a attribute called cmd
    sub = p.add_subparsers(dest="cmd", required=True)

    # NOTE: this register the run subcommand
    run = sub.add_parser("run")

    run.add_argument("--dataset", default="golden")
    run.add_argument("--target", default="atlas", choices=TARGETS)
    run.add_argument("--label", default="no label")
    run.add_argument("--repeats", type=int, default=2)
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--resume", help="path of a run file to continue")
    run.add_argument("--warmup", type=int, default=2,
                     help="untimed queries sent before the run starts")
    run.add_argument("--target-config", action="append", metavar="KEY=VALUE",
                     help="target settings to record in the run, e.g. "
                          "retrieval_mode=hybrid_graph (repeatable)")

    rep = sub.add_parser("report")
    rep.add_argument("run", help="path of a run file")

    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("baseline")
    cmp_.add_argument("candidate")
    cmp_.add_argument("--min-effect", type=float, default=0.02,
                      help="smallest change in a 0-1 rate worth calling real")
    cmp_.add_argument("--min-rel", type=float, default=0.10,
                      help="smallest relative change in latency/tokens worth calling real")

    return p.parse_args()


commands = {"run": cmd_run, "report": cmd_report, "compare": cmd_compare}


def main():
    args = parse_args()
    commands[args.cmd](args)


if __name__ == "__main__":
    main()

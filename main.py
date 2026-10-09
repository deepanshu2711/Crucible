import json
import os
import time
import requests
import argparse
import re
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    }


def summarise(results):
    ok = [r for r in results if "error" not in r]
    n = len(ok)
    if n == 0:
        print("\nNo successful cases.")
        return {"cases": len(results), "errors": len(results)}

    def rate(pred):
        return sum(1 for r in ok if pred(r)) / n

    def avg(key):
        vals = [r[key] for r in ok if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0

    s = {
        "cases": len(results),
        "errors": len(results) - n,
        "hit@5": rate(lambda r: r["rank"] is not None and r["rank"] <= 5),
        "hit@all": rate(lambda r: r["rank"] is not None),
        "answer_ok": rate(lambda r: r["answer_ok"]),
        "avg_latency_s": avg("latency_s"),
        "avg_tokens": avg("tokens"),
    }
    print(
        f"\nHIT@5 {s['hit@5']:.2f}   HIT@ALL {s['hit@all']:.2f}   "
        f"ANSWER {s['answer_ok']:.2f}"
    )
    print(
        f"avg latency {s['avg_latency_s']:.0f}s   avg tokens {
            s['avg_tokens']:.0f}   "
        f"errors {s['errors']}"
    )
    return s


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
            elif kind in ("case"):
                row.setdefault("repeat_idx", 0)
                row.setdefault("error", None)
                attempts.append(row)

    return config, attempts


def run_one(case, repeat_idx, url):
    return


def cmd_run(args):
    if args.resume:
        run_path = args.run_path
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

        random.shuffle(jobs)
        print(f"{run_path}\n{len(cases)} cases x {config['repeats']} repeats, "
              f"{len(done)} done, {len(jobs)} to go\n")
        pool = ThreadPoolExecutor(max_workers=config.get("concurrency", 1))
        futures = [
            pool.submit(run_one, c, r, config["atlas_url"])
            for c, r in jobs
        ]


def cmd_report():
    return


def cmd_compare():
    return


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


conmmands = {"run": cmd_run, "report": cmd_report, "compare": cmd_compare}


def main():
    args = parse_args()
    conmmands[args.cmd](args)
    # dataset = f"datasets/{args.dataset}.jsonl"
    # print(dataset)
    # url = TARGETS[args.target]
    # label = args.label
    # cases = load_cases(dataset)
    # os.makedirs(RUNS_DIR, exist_ok=True)
    # run_path = os.path.join(
    #     RUNS_DIR, f"run_{time.strftime('%Y%m%d-%H%M%S')}_{slug(label)}.jsonl")
    #
    # with open(run_path, "w") as f:
    #     f.write(json.dumps(
    #         {"type": "config", "label": label, "atlas_url": url, "dataset": dataset, "cases": len(cases)}) + "\n")
    #     results = []
    #     for case in cases:
    #         try:
    #             r = score(case, ask_atlas(case, url))
    #             rank = r["rank"] if r["rank"] is not None else "-"
    #             verdict = "OK   " if r["answer_ok"] else "wrong"
    #             print(f"{case['id']}  rank {rank:>2} {verdict}")
    #         except Exception as e:
    #             r = {"id": case["id"], "error": str(e)}
    #             print(f"{case['id']}  ERROR  {e}")
    #
    #         results.append(r)
    #         f.write(json.dumps({"type": "case", **r}) + "\n")
    #         f.flush()
    #
    #     summary = summarise(results)
    #     f.write(json.dumps({"type": "summary", **summary}) + "\n")
    #
    # print(f"\nSaved to {run_path}")


if __name__ == "__main__":
    main()

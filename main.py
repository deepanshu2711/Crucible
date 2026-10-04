import json
import os
import time
import sys
import requests


DATASET = "datasets/golden.jsonl"
ATLAS_URL = "http://localhost:8080/api/v1/query"
RUNS_DIR = "runs"
TIMEOUT_S = 600


def load_cases(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def ask_atlas(case):
    body = {"document_id": case["document_id"], "query": case['question']}
    resp = requests.post(ATLAS_URL, json=body, timeout=TIMEOUT_S)
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
    trace = out.get("trace", [])

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


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "no label"
    cases = load_cases(DATASET)
    os.makedirs(RUNS_DIR, exist_ok=True)
    run_path = os.path.join(
        RUNS_DIR, f"run_{time.strftime('%Y%m%d-%H%M%S')}.jsonl")

    with open(run_path, "w") as f:
        f.write(json.dumps(
            {"type": "config", "label": label, "atlas_url": ATLAS_URL, "dataset": DATASET, "cases": len(cases)}) + "\n")
        results = []
        for case in cases:
            try:
                r = score(case, ask_atlas(case))
                rank = r["rank"] if r["rank"] is not None else "-"
                verdict = "OK   " if r["answer_ok"] else "wrong"
                print(f"{case['id']}  rank {rank:>2} {verdict}")
            except Exception as e:
                r = {"id": case["id"], "error": str(e)}
                print(f"{case['id']}  ERROR  {e}")

            results.append(r)
            f.write(json.dumps({"type": "case", **r}) + "\n")
            f.flush()

        summary = summarise(results)
        f.write(json.dumps({"type": "summary", **summary}) + "\n")

    print(f"\nSaved to {run_path}")


if __name__ == "__main__":
    main()

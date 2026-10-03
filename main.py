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


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "no label"
    cases = load_cases(DATASET)[:3]
    os.makedirs(RUNS_DIR, exist_ok=True)
    run_path = os.path.join(
        RUNS_DIR, f"run_{time.strftime('%Y%m%d-%H%M%S')}.jsonl")

    with open(run_path, "w") as f:
        f.write(json.dumps(
            {"type": "config", "label": label, "atlas_url": ATLAS_URL, "dataset": DATASET, "cases": len(cases)}) + "\n")
        results = []
        for case in cases:
            try:
                resp = ask_atlas(case)
                print(resp)
            except Exception as e:
                print(e)


if __name__ == "__main__":
    main()

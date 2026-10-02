import json
import os
import time
import sys


DATASET = "datasets/golden.jsonl"
ATLAS_URL = "http://localhost:8080/api/v1/documents"
RUNS_DIR = "runs"


def load_cases(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "no label"
    cases = load_cases(DATASET)
    os.makedirs(RUNS_DIR, exist_ok=True)
    run_path = os.path.join(
        RUNS_DIR, f"run_{time.strftime('%Y%m%d-%H%M%S')}.jsonl")

    with open(run_path, "w") as f:
        f.write(json.dumps(
            {"type": "config", "label": label, "atlas_url": ATLAS_URL, "dataset": DATASET, "cases": len(cases)}) + "\n")


if __name__ == "__main__":
    main()

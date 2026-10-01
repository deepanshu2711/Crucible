import json

ATLAS = "http://localhost:8080/api/v1/documents"
cases = [json.loads(l) for l in open("datasets/golden.jsonl")]
results = []

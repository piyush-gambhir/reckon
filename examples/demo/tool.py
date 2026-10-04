"""Synthetic CLI output only. This executable never connects to a provider."""
import json
from datetime import datetime
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
if name in ("cubeapm", "grafana") and (args[:2] == ["metrics", "query-range"] or args[:2] == ["datasource", "query"]):
    start = args[args.index("--from") + 1]
    timestamp = int(datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp())
    baseline = "13:30:00" in start
    query = args[2] if name == "cubeapm" else args[args.index("--expr") + 1]
    value = 20 if "request" in query or "count" in query and "latency_sum" not in query else 100 if baseline else 850
    print(json.dumps({"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"service": "checkout", "fixture": "synthetic"}, "values": [[timestamp, str(value)], [timestamp + 60, str(value)]]}
    ]}}))
elif name == "cubeapm" and args[:2] == ["logs", "query"]:
    print(json.dumps({"_time": "2026-09-08T14:03:00Z", "_msg": "Synthetic fixture: pricing request timed out", "service": "checkout"}))
elif name == "grafana" and args[:2] == ["annotation", "list"]:
    print(json.dumps([{"time": 1788876060000, "text": "Synthetic deploy checkout #42", "tags": ["checkout"]}]))
elif name == "jenkins" and args[:2] == ["build", "list"]:
    print(json.dumps([{"number": 42, "result": "SUCCESS", "timestamp": 1788876060000, "description": "Synthetic fixture; inspect build 42 to verify deployed revision"}]))
else:
    print("Unsupported synthetic command", file=sys.stderr)
    sys.exit(1)

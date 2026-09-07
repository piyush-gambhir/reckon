"""A repeatable offline walkthrough using the real collection code."""
from pathlib import Path
import os
import shlex
import shutil
import sys
import uuid

from .investigation import collect, note, report, resume
from .store import create, private_dir, read_json, write, write_json


def demo(root):
    if os.name == "nt":
        raise ValueError("the synthetic executable demo currently requires macOS/Linux or WSL2")
    target = root / ".reckon-demo" / uuid.uuid4().hex[:10]
    private_dir(root / ".reckon-demo")
    for directory in ("scripts", "bin", "infra-knowledge/staging", "infra-knowledge/_shared"):
        private_dir(target / directory)
    shutil.copyfile(root / ".envrc", target / ".envrc")
    shutil.copyfile(root / "scripts/integrations.tsv", target / "scripts/integrations.tsv")
    tool = (root / "examples/demo/tool.py").read_text()
    for binary in ("cubeapm", "grafana", "jenkins"):
        write(target / "bin" / binary, "#!" + sys.executable + "\n" + tool)
        (target / "bin" / binary).chmod(0o700)
    # Only fixture binaries + essential system programs are on the child PATH.
    # Missing optional sources cannot accidentally resolve a real authenticated CLI.
    write(target / ".env.staging", "\n".join([
        "PATH=" + shlex.quote(str(target / "bin") + ":/usr/bin:/bin"),
        "GRAFANA_URL=https://synthetic.invalid", "GRAFANA_TOKEN=synthetic-token",
        "CUBEAPM_HOST=synthetic.invalid", "CUBEAPM_EMAIL=fixture@synthetic.invalid", "CUBEAPM_PASSWORD=synthetic-password",
        "JENKINS_URL=https://synthetic.invalid", "JENKINS_USER=fixture", "JENKINS_TOKEN=synthetic-token", ""]))
    write(target / ".reckon-env", "staging\n")
    mapping = read_json(root / "examples/services.json")
    mapping["services"][0]["sources"].pop("github", None)
    write_json(target / "infra-knowledge/staging/services.json", mapping)
    path, session = create(target, "staging", mapping["services"][0],
                           "Synthetic demo: checkout latency increased around a deployment",
                           {"from": "2026-09-08T14:00:00Z", "to": "2026-09-08T14:30:00Z"}, "latency", "debug")
    records = collect(target, session["id"], "staging")
    metric_refs = [r["id"] for r in records if r["label"].startswith("latency_ms") and r["status"] == "ok"]
    if len(metric_refs) != 2 or any(r["status"] != "ok" for r in records):
        raise ValueError("synthetic collection failed; inspect " + str(path))
    baseline = next(r["series"][0]["mean"] for r in records if r["label"] == "latency_ms / baseline")
    current = next(r["series"][0]["mean"] for r in records if r["label"] == "latency_ms / incident")
    note(target, session["id"], "finding", f"Synthetic latency samples changed from {baseline:g} ms in the baseline to {current:g} ms in the incident window.", metric_refs)
    note(target, session["id"], "hypothesis", "The nearby deployment may explain the slowdown; timing alone is insufficient.", [],
         "Inspect build 42, establish the deployed SHA, and test its changes against pricing traces and request volume.")
    return {"synthetic": True, "network_used": False, "workspace": str(target), "session": session["id"],
            "report": str(report(target, session["id"])), "evidence_records": len(resume(target, session["id"])["evidence"]),
            "resume": "python3 scripts/reckon.py --root " + shlex.quote(str(target)) + " resume " + session["id"]}

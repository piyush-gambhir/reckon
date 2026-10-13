"""A repeatable offline walkthrough using the real connection and collection code."""
import os
import shlex
import sys
import uuid

from . import orgs
from .investigation import collect, note, report, resume
from .store import create, local_path, private_dir, read_json, write, write_json


def demo(h):
    if os.name == "nt":
        raise ValueError("the synthetic executable demo currently requires macOS/Linux or WSL2")
    code = orgs.code_root()
    registry = orgs.providers(code)
    # A throwaway Reckon home: the demo never sees your orgs or credentials.
    home = local_path(h, "demos", uuid.uuid4().hex[:10])
    private_dir(local_path(home, "bin"))
    tool = (code / "examples/demo/tool.py").read_text()
    for binary in ("cubeapm", "grafana", "jenkins"):
        write(home / "bin" / binary, "#!" + sys.executable + "\n" + tool)
        (home / "bin" / binary).chmod(0o700)
    data = orgs.create(home, "demo", "Synthetic demo", ["staging"], [], registry)
    data["connections"] = {"cubeapm": {"provider": "cubeapm"}, "grafana": {"provider": "grafana"},
                           "jenkins-staging": {"provider": "jenkins", "env": "staging"}}
    orgs.save(home, data, registry)
    for cname, lines in (("cubeapm", ["CUBEAPM_SERVER=https://synthetic.invalid", "CUBEAPM_EMAIL=fixture@synthetic.invalid", "CUBEAPM_PASSWORD=synthetic-password"]),
                         ("grafana", ["GRAFANA_URL=https://synthetic.invalid", "GRAFANA_TOKEN=synthetic-token"]),
                         ("jenkins-staging", ["JENKINS_URL=https://synthetic.invalid", "JENKINS_USER=fixture", "JENKINS_TOKEN=synthetic-token"])):
        path = orgs.secrets_path(home, "demo", cname)
        write(path, "\n".join(lines) + "\n")
        path.chmod(0o600)
    root = orgs.workspace(home, "demo")
    mapping = read_json(code / "examples/services.json")
    mapping["services"][0]["sources"].pop("github", None)
    write_json(local_path(root, "knowledge", "staging", "services.json"), mapping)
    ctx = orgs.context(home, "demo", "staging", registry)
    path, session = create(root, "demo", "staging", "Synthetic demo: checkout latency increased around a deployment", "debug",
                           mapping["services"][0], {"from": "2026-09-08T14:00:00Z", "to": "2026-09-08T14:30:00Z"}, "latency")
    # Only the fixture binaries and essential system programs are on the child PATH,
    # so a missing source can never resolve a real, authenticated CLI.
    saved = os.environ.get("PATH")
    os.environ["PATH"] = str(home / "bin") + ":/usr/bin:/bin"
    try:
        records = collect(home, ctx, root, session["id"], registry=registry)
    finally:
        os.environ["PATH"] = saved or ""
    metric_refs = [r["id"] for r in records if r["label"].startswith("latency_ms") and r["status"] == "ok"]
    if len(metric_refs) != 2 or any(r["status"] != "ok" for r in records):
        raise ValueError("synthetic collection failed; inspect " + str(path))
    baseline = next(r["series"][0]["mean"] for r in records if r["label"] == "latency_ms / baseline")
    current = next(r["series"][0]["mean"] for r in records if r["label"] == "latency_ms / incident")
    note(root, session["id"], "finding", f"Synthetic latency samples changed from {baseline:g} ms in the baseline to {current:g} ms in the incident window.", metric_refs)
    note(root, session["id"], "hypothesis", "The nearby deployment may explain the slowdown; timing alone is insufficient.", [],
         "Inspect build 42, establish the deployed SHA, and test its changes against pricing traces and request volume.")
    return {"synthetic": True, "network_used": False, "home": str(home), "session": session["id"], "org": "demo", "environment": "staging",
            "report": str(report(root, session["id"])), "evidence_records": len(resume(root, session["id"])["evidence"]),
            "resume": "RECKON_HOME=" + shlex.quote(str(home)) + " reckon resume " + session["id"] + " --org demo --env staging"}

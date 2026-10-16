"""Command line: `triangulate run`, `reason`, `eval`, `review`, `load`."""

from __future__ import annotations

import json

import typer
import yaml
from rich.console import Console
from rich.table import Table

from .config import CONFIG_DIR
from .runstore import RunStore

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False, help="Triangulate: requirements x live UI x code, joined in Neo4j.")
console = Console()


@app.command()
def run(profile: str = typer.Option("default", help="Config profile: default or smoke"),
        offline: bool = typer.Option(False, help="Use only cached LLM responses; a cache miss is an error"),
        until: str = typer.Option(None, help="Stop after this stage"),
        start: str = typer.Option(None, help="Resume an existing run from this stage"),
        run_id: str = typer.Option(None, "--run", help="Existing run id to resume (with --start)")):
    """Run the build pipeline (pin -> ingest -> code -> crawl -> link -> graph)."""
    from .pipeline import Pipeline
    if start and not run_id:
        raise typer.BadParameter("--start resumes an existing run: pass --run <run_id> too")
    rs = RunStore.open(run_id) if run_id else None
    p = Pipeline(profile, offline, rs)
    console.print(f"[bold]run[/] {p.run.id}  profile={p.cfg.profile}  offline={offline}")
    p.run_all(until=until, start=start)
    console.print(f"[green]done[/] -> runs/{p.run.id}")


@app.command()
def reason(pr: int = typer.Argument(..., help="Pull request number in the code repo"),
           run_id: str = typer.Option("latest", "--run"),
           offline: bool = typer.Option(False, help="Use only cached LLM/GitHub responses"),
           strict: bool = typer.Option(False, help="Exit non-zero when the report has open 'needs a human' items")):
    """Blast radius of a pull request -> runs/<run>/reason/pr-<n>/report.html"""
    from .reason.run import run_reason
    out = run_reason(RunStore.open(run_id), pr, offline=offline)
    console.print(json.dumps(out["summary"], indent=1))
    console.print(f"[green]report[/] {out['report_html']}")
    if strict and out["summary"]["needs_human"]:
        raise typer.Exit(code=2)


@app.command("eval")
def eval_cmd(run_id: str = typer.Option("latest", "--run"),
             compare: list[str] = typer.Option(None, help="Other run ids to compare stage hashes with"),
             update_calibration: bool = typer.Option(False, help="Write the measured link precision to "
                                                      "eval/calibration.json for the next run")):
    """Invariants + gold-set metrics + repeatability, and a correct/incorrect verdict for the run."""
    from .eval.harness import run_eval
    res = run_eval(RunStore.open(run_id), compare or [], update_calibration)
    console.print_json(json.dumps(res))
    v = res["verdict"]
    console.print("[green]run is correct[/] (invariants + eval/thresholds.yaml)" if v["correct"]
                  else "[red]run is NOT correct:[/] " + "; ".join(v["failed"]))
    if not v["correct"]:
        raise typer.Exit(1)


@app.command()
def load(run_id: str = typer.Option("latest", "--run")):
    """Rebuild the Neo4j graph from a run's artifacts."""
    from .config import load_secrets
    from .graph.db import Graph
    from .graph.loader import load_base, load_links
    rs = RunStore.open(run_id)
    g = Graph(load_secrets(require_llm=False))
    try:
        console.print({**load_base(g, rs), **load_links(g, rs)})
    finally:
        g.close()


@app.command()
def export(run_id: str = typer.Option("latest", "--run")):
    """Copy a run's PR reports and a summary into samples/ (the reviewable sample output)."""
    import shutil

    from .config import ROOT
    rs = RunStore.open(run_id)
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    reason_dir = rs.dir / "reason"
    prs = sorted(p.name for p in reason_dir.iterdir() if p.is_dir()) if reason_dir.exists() else []
    for pr in prs:
        dest = out / pr
        dest.mkdir(exist_ok=True)
        for f in ("report.html", "report.md", "impact.json"):
            if (reason_dir / pr / f).exists():
                shutil.copy2(reason_dir / pr / f, dest / f)
    m = rs.manifest
    summary = {"run_id": rs.id, "app": m["app"], "code": {k: m["code"][k] for k in ("repo", "tag", "sha")},
               "docs": {k: m["docs"][k] for k in ("repo", "sha")}, "llm": m.get("llm"), "stage_stats": m.get("stage_stats"),
               "stage_hashes": m.get("stage_hashes"), "llm_usage": m.get("llm_usage"), "link_priors": m.get("link_priors"),
               "eval": rs.read_json("eval/results.json") if rs.exists("eval/results.json") else None}
    # Paths relative to the project, so the published summary does not carry this machine's home folder.
    text = json.dumps(summary, indent=1, sort_keys=True).replace(json.dumps(str(ROOT) + "/")[1:-1], "")
    (out / "run-summary.json").write_text(text + "\n")
    console.print(f"[green]exported[/] {len(prs)} report(s) and run-summary.json to {out}")


@app.command()
def review(run_id: str = typer.Option("latest", "--run")):
    """Walk through open review items; answers go to config/overrides.yaml and are replayed on every run."""
    rs = RunStore.open(run_id)
    items = rs.read_jsonl("link/review_items.jsonl")
    if not items:
        console.print("No open review items.")
        return
    path = CONFIG_DIR / "overrides.yaml"
    ov = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    ov.setdefault("requirements", {})
    reqs = {r["id"]: r for r in rs.read_jsonl("ingest/requirements.jsonl")}
    t = Table("id", "trigger", "question")
    for it in items:
        t.add_row(it["id"], it["trigger"], it["question"])
    console.print(t)
    for it in items:
        rid = it.get("req_id")
        if not rid:
            continue
        console.print(f"\n[bold]{it['question']}[/]\n  doc: {reqs.get(rid, {}).get('url', '')}")
        ans = typer.prompt("  covered / not_covered / skip", default="skip")
        if ans in ("covered", "not_covered"):
            note = typer.prompt("  note", default="")
            entry = {"status": ans, "note": note}
            if ans == "covered":
                entry["elements"] = [e.strip() for e in typer.prompt("  element ids (comma separated)").split(",") if e.strip()]
            ov["requirements"][rid] = entry
    path.write_text(yaml.safe_dump(ov, sort_keys=True))
    console.print(f"[green]saved[/] {path}. Re-run from the 'domcode' stage to apply.")


if __name__ == "__main__":
    app()

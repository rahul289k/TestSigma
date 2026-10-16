"""Stage 7 entry point: PR number -> impact.json + report.html + report.md."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import load_config, load_secrets
from ..graph.db import Graph
from ..graph.loader import load_base, load_links
from ..llm.client import LLM
from ..runstore import RunStore
from .changes import map_changes
from .github import GitHub
from .impact import compute_impact
from .plain import route_name
from .writer import write_report_text

TEMPLATES = Path(__file__).parent / "templates"
STATUS_LABEL = {"LOSES_COVERAGE": "Loses coverage", "AT_RISK": "At risk", "WEAK_SIGNAL": "Weak signal"}
GAP_LABEL = {"UNOBSERVED_IN_UI": "built, not reached by the crawl", "OUTSIDE_CRAWL_BOUNDARY": "needs sign-in or the desktop app",
             "NOT_FOUND_ANYWHERE": "not found", "OUTSIDE_BOUNDARY_UNVERIFIED": "needs sign-in, unverified",
             "NOT_UI_TESTABLE": "not a web UI feature"}


def _route_label(route: str) -> str:
    return route_name(route)


def _area(cid: str, screens: list[dict], elements_by_comp: dict[str, list[str]]) -> str:
    labels = [s["label"] for s in screens if set(e["eid"] for e in s["elements"]) & set(elements_by_comp.get(cid, []))]
    if labels:
        return labels[0]
    parts = [p for p in cid.removeprefix("src/components/").removesuffix(".vue").split("/") if p not in ("index",)]
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", " ".join(parts)).lower()
    return f"the {words} area"


def run_reason(run: RunStore, pr_number: int, offline: bool = False) -> dict:
    m = run.manifest
    cfg = load_config(m.get("profile"))
    secrets = load_secrets(require_llm=not offline)
    out_dir = f"reason/pr-{pr_number}"
    gh = GitHub(cfg.code.repo, secrets.github_token, offline=offline)
    pr = gh.pull(pr_number)
    files = gh.pull_files(pr_number)
    mapped = map_changes(cfg, gh, pr, files, Path(m["code"]["path"]), run.path(f"{out_dir}/work/x").parent)
    code = run.read_json("code/code_graph.json")
    g = Graph(secrets)
    try:
        load_base(g, run)
        load_links(g, run)
        crawled = {s["route"] for s in run.read_jsonl("crawl/states.jsonl")}
        impact = compute_impact(cfg, g, code, mapped, crawled)
        # The PR joins the graph, so its blast radius can also be queried in Cypher (docs/DESIGN.md §3, Q1). A changed
        # file that is not in the code graph (tests, other packages, new files) simply gets no CHANGES edge.
        weights = cfg.reason.change_weights
        g.merge_nodes("PullRequest", [{"number": pr["number"], "title": pr["title"], "url": pr["html_url"],
                                       "overall_risk": impact["summary"]["overall_risk"]}])
        g.merge_rels("CHANGES", "PullRequest", "File", [
            {"a": pr["number"], "b": ch["file"],
             "props": {"kinds": ch.get("kinds", []), "weight": max((weights.get(k, 0.0) for k in ch.get("kinds", [])), default=0.0)}}
            for ch in mapped["changes"]])
    finally:
        g.close()

    el_comp: dict[str, list[str]] = {}
    for r in run.read_jsonl("link/dom_code.jsonl"):
        for c in r["components"]:
            el_comp.setdefault(c["component"], []).append(r["eid"])
    areas = []
    for ch in mapped["changes"]:
        if ch["type"] == "component":
            areas.append({"file": ch["file"], "area": _area(ch["previous"] or ch["file"], impact["screens"], el_comp)})
        elif ch["type"] == "ts":
            areas.append({"file": ch["file"], "area": "shared logic behind the screens listed"})
        elif ch["type"] == "i18n":
            areas.append({"file": ch["file"], "area": "wording of on-screen text"})
        elif ch["type"] == "test":
            areas.append({"file": ch["file"], "area": "automated tests (no user impact)"})
    impact["change_areas"] = areas
    pr_info = {"number": pr["number"], "title": pr["title"], "body": pr.get("body") or "", "html_url": pr["html_url"],
               "state": pr["state"], "merged": bool(pr.get("merged_at")), "user": pr["user"]["login"],
               "base_sha": pr["base"]["sha"], "head_sha": pr["head"]["sha"], "base_ref": pr["base"]["ref"]}
    impact["pr"] = pr_info
    impact["changes"] = mapped["changes"]
    run.write_json(f"{out_dir}/impact.json", impact)

    llm = LLM(cfg.llm, secrets.gemini_api_key or None, run, offline=offline)
    text, first_errors = write_report_text(llm, pr_info, impact, f"report:pr{pr_number}")
    run.write_json(f"{out_dir}/report_text.json", {"text": text.model_dump(), "first_attempt_errors": first_errors})

    # ---- render
    labels = {s["fp"]: s for s in impact["screens"]}
    states = {s["fp"]: s for s in run.read_jsonl("crawl/states.jsonl")}
    if run.exists("probe/states.jsonl"):
        states.update({s["fp"]: s for s in run.read_jsonl("probe/states.jsonl")})
    vw, vh = cfg.crawl.viewport["width"], cfg.crawl.viewport["height"]

    def shot(fp: str, eids: list[str]) -> dict | None:
        st = states.get(fp)
        if not st or not run.exists(st["screenshot"]):
            return None
        src = "data:image/jpeg;base64," + base64.b64encode(run.path(st["screenshot"]).read_bytes()).decode()
        boxes = []
        for e in st["elements"]:
            if e["eid"] in eids:
                x, y, w_, h_ = e["bbox"]
                if y < vh:
                    boxes.append([round(100 * x / vw, 2), round(100 * y / vh, 2), round(100 * w_ / vw, 2), round(100 * h_ / vh, 2)])
        return {"src": src, "boxes": boxes[:6], "label": labels.get(fp, {}).get("label") or st.get("title", "")}

    flows = {f["id"]: f for f in impact["flows"]}
    retest = []
    seen = set()
    for r in text.retest:
        f = flows[r.flow_id]
        seen.add(f["id"])
        state = states.get(f["target_state"], {})
        target_label = next((e["name"] for e in state.get("elements", []) if e["eid"] in f["touches"]), f["name"])
        retest.append({"flow": {**f, "start_label": _route_label(f["start_route"])}, "why": r.why,
                       "target_label": target_label, "shot": shot(f["target_state"], f["touches"])})
    for f in impact["flows"]:
        if f["id"] not in seen and f["band"] != "Low":
            state = states.get(f["target_state"], {})
            target_label = next((e["name"] for e in state.get("elements", []) if e["eid"] in f["touches"]), f["name"])
            retest.append({"flow": {**f, "start_label": _route_label(f["start_route"])}, "why": "", "target_label": target_label,
                           "shot": None})
    snotes = {n.id: n.note for n in text.screen_notes}
    all_screens = [{**s, "note": snotes.get(s["fp"], ""), "controls": ", ".join(e["label"] for e in s["elements"][:8])}
                   for s in impact["screens"]]
    screens = [s for s in all_screens if s["band"] != "Low"]
    low_screens = [s for s in all_screens if s["band"] == "Low"]
    rnotes = {n.id: n.note for n in text.requirement_notes}
    all_reqs = [{**q, "note": rnotes.get(q["id"], ""), "status_label": STATUS_LABEL[q["status"]]} for q in impact["requirements"]]
    reqs = [q for q in all_reqs if q["status"] != "WEAK_SIGNAL"]
    weak_reqs = [q for q in all_reqs if q["status"] == "WEAK_SIGNAL"]
    gaps = [{**gp, "kind_label": GAP_LABEL.get(gp["kind"], gp["kind"])} for gp in impact["gaps_touched"]]
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    html = env.get_template("report.html.j2").render(
        pr=pr_info, manifest=m, summary=impact["summary"], text=text, retest=retest, screens=screens, low_screens=low_screens,
        requirements=reqs, weak_requirements=weak_reqs,
        gaps=gaps, unmapped=impact["unmapped"] + impact["outside"], elements=impact["elements"], changes=mapped["changes"],
        outside=mapped["outside"])
    run.path(f"{out_dir}/report.html").write_text(html)
    md = _markdown(pr_info, impact, text, retest, all_reqs)
    run.path(f"{out_dir}/report.md").write_text(md)
    return {"summary": impact["summary"], "report_html": str(run.path(f"{out_dir}/report.html")),
            "first_attempt_errors": first_errors}


def _markdown(pr: dict, impact: dict, text, retest: list[dict], reqs: list[dict]) -> str:
    s = impact["summary"]
    lines = [f"# PR #{pr['number']}: {pr['title']}", "", f"**{s['overall_risk']} risk.** {text.headline}", "", text.what_changed, "",
             "## Retest these first", ""]
    for r in retest:
        f = r["flow"]
        steps = " → ".join([f"Open {f['start_label']}"] + [f"click **{x}**" for x in f["steps"]] + [f"check **{r['target_label']}**"])
        lines += [f"- **[{f['band']}] {f['name']}** - {r['why']}", f"  - {steps}"]
    lines += ["", "## Screens affected", ""]
    for sc in impact["screens"]:
        lines.append(f"- [{sc['band']}] {sc['label']}: {', '.join(e['label'] for e in sc['elements'][:8])}")
    lines += ["", "## Product requirements", ""]
    for q in reqs:
        lines += [f"- **{q['status_label']}**: {q['statement']} - {q['note']}", f"  > \"{q['quote']}\" ({q['url']})"]
    lines += ["", "## Needs a human", ""] + [f"- {q.question}" for q in text.questions]
    lines += ["", "## Changes we could not connect to a screen", ""] + [f"- {u['why']}" for u in impact["unmapped"] + impact["outside"]]
    return "\n".join(lines) + "\n"

"""Integration test: the blast-radius traversal against a real Neo4j database (a scratch database)."""

import pytest

from triangulate.config import load_config, load_secrets
from triangulate.graph.db import Graph
from triangulate.reason.impact import compute_impact

pytestmark = pytest.mark.neo4j
DB = "triangulate-test"

A, B, C = "src/components/a/Panel.vue", "src/components/b/Child.vue", "src/components/c/User.vue"
X = "src/helpers/x.ts"


@pytest.fixture(scope="module")
def g():
    secrets = load_secrets(require_llm=False)
    sys = Graph(secrets, database="system")
    sys.run(f"CREATE DATABASE `{DB}` IF NOT EXISTS WAIT")
    sys.close()
    graph = Graph(secrets, database=DB)
    graph.ensure_schema()
    graph.wipe()
    graph.merge_nodes("Component", [{"id": A, "name": "APanel"}, {"id": B, "name": "BChild"}, {"id": C, "name": "CUser"}])
    graph.merge_nodes("File", [{"path": p} for p in (A, B, C, X)])
    graph.merge_rels("RENDERS", "Component", "Component", [{"a": A, "b": B, "props": {}}])
    graph.merge_rels("IMPORTS", "File", "File", [{"a": C, "b": X, "props": {"names": ["foo"], "namespace": False, "default": ""}}])
    graph.merge_nodes("Element", [{"id": f"E{i}", "label": f"control {i}", "role": "button", "route": "/"} for i in range(1, 5)])
    graph.merge_nodes("I18nKey", [{"key": "k.removed", "text": "Gone"}])
    graph.merge_rels("DISPLAYS", "Element", "I18nKey", [{"a": "E4", "b": "k.removed", "props": {}}])
    graph.merge_rels("IMPLEMENTED_BY", "Element", "Component",
                     [{"a": e, "b": c, "props": {"method": "i18n_route_unique", "confidence": 0.9, "evidence": "t"}}
                      for e, c in (("E1", A), ("E2", B), ("E3", C))])
    graph.merge_nodes("State", [{"fp": "S1", "label": "Screen one", "route": "/", "screenshot": "x", "depth": 0, "path": "[]"},
                                {"fp": "S2", "label": "Screen two", "route": "/", "screenshot": "y", "depth": 1, "path": "[]"}])
    graph.merge_rels("CONTAINS", "State", "Element", [{"a": s, "b": e, "props": {"bbox": [0, 0, 10, 10]}}
                                                      for s, e in (("S1", "E1"), ("S1", "E2"), ("S2", "E3"), ("S2", "E4"))])
    graph.merge_nodes("Requirement", [{"id": "R1", "statement": "s1", "quote": "q1", "url": "u", "feature": "f"},
                                      {"id": "R2", "statement": "s2", "quote": "q2", "url": "u", "feature": "f"}])
    graph.merge_rels("COVERED_BY", "Requirement", "Element", [{"a": "R1", "b": "E1", "props": {"confidence": 0.8}},
                                                              {"a": "R2", "b": "E4", "props": {"confidence": 0.8}}])
    graph.merge_nodes("Flow", [{"id": "F1", "name": "Use panel", "steps": "[]", "start_route": "/", "target_state": "S1"}])
    graph.merge_rels("STEP", "Flow", "Element", [{"a": "F1", "b": "E1", "props": {"order": 0}}], rel_key="order")
    graph.merge_rels("EXERCISES", "Flow", "Requirement", [{"a": "F1", "b": "R1", "props": {}}])
    yield graph
    graph.wipe()
    graph.close()


def test_blast_radius_traversal(g):
    cfg = load_config()
    code = {"components": [{"id": A, "name": "APanel", "renders": [B]}, {"id": B, "name": "BChild", "renders": []},
                           {"id": C, "name": "CUser", "renders": []}],
            "routes": [{"path": "/", "reachable": [A, B, C]}]}
    mapped = {"outside": [], "changes": [
        {"type": "component", "file": A, "previous": None, "kinds": ["template"], "keys_removed": [], "drift": False},
        {"type": "ts", "file": X, "previous": None, "kinds": ["symbol"], "symbols": ["foo"], "drift": False},
        {"type": "i18n", "file": "locales/en.json", "previous": None, "kinds": ["i18n"], "keys_removed": ["k.removed"],
         "keys_changed": [], "drift": False},
    ]}
    imp = compute_impact(cfg, g, code, mapped, {"/"})
    risk = {e["eid"]: (round(e["risk"], 3), e["band"]) for e in imp["elements"]}
    assert risk["E1"] == (0.9, "High")      # direct template change x unique link
    assert risk["E2"] == (0.54, "Medium")   # child placed by the changed template (0.6 factor)
    assert risk["E3"] == (0.63, "Medium")   # one import hop from the changed helper (0.7 decay)
    assert risk["E4"][1] == "High"          # its text was removed
    reqs = {q["id"]: q["status"] for q in imp["requirements"]}
    assert reqs == {"R1": "AT_RISK", "R2": "LOSES_COVERAGE"}
    assert [f["id"] for f in imp["flows"]] == ["F1"] and imp["flows"][0]["band"] == "High"
    assert imp["summary"]["overall_risk"] == "High"


def test_component_dropping_a_key_only_affects_its_own_elements(g):
    cfg = load_config()
    g.merge_nodes("I18nKey", [{"key": "action.save", "text": "Save"}])
    g.merge_rels("DISPLAYS", "Element", "I18nKey", [{"a": "E1", "b": "action.save", "props": {}},
                                                    {"a": "E3", "b": "action.save", "props": {}}])
    code = {"components": [{"id": A, "name": "APanel", "renders": [B]}, {"id": B, "name": "BChild", "renders": []},
                           {"id": C, "name": "CUser", "renders": []}],
            "routes": [{"path": "/", "reachable": [A, B, C]}]}
    mapped = {"outside": [], "changes": [{"type": "component", "file": A, "previous": None, "kinds": ["script"],
                                          "keys_removed": ["action.save"], "drift": False}]}
    imp = compute_impact(cfg, g, code, mapped, {"/"})
    lost = {e["eid"] for e in imp["elements"] if e["lost"]}
    assert lost == {"E1"}                    # E3 shows "Save" too, but is drawn by another component


def test_single_generic_control_does_not_place_a_component_on_screen(g):
    cfg = load_config()
    # E5 is a generic control linked to A by tie-break and is the ONLY A-control on screen S3.
    g.merge_nodes("Element", [{"id": "E5", "label": "Wrap lines", "role": "button", "route": "/"}])
    g.merge_nodes("State", [{"fp": "S3", "label": "Unrelated editor", "route": "/", "screenshot": "z", "depth": 1, "path": "[]"}])
    g.merge_rels("IMPLEMENTED_BY", "Element", "Component",
                 [{"a": "E5", "b": A, "props": {"method": "i18n_route_tiebreak", "confidence": 0.6, "evidence": "t"}}])
    g.merge_rels("CONTAINS", "State", "Element", [{"a": "S3", "b": "E5", "props": {"bbox": [0, 0, 5, 5]}}])
    code = {"components": [{"id": A, "name": "APanel", "renders": []}], "routes": [{"path": "/", "reachable": [A]}]}
    mapped = {"outside": [], "changes": [{"type": "component", "file": A, "previous": None, "kinds": ["script"],
                                          "keys_removed": [], "drift": False}]}
    imp = compute_impact(cfg, g, code, mapped, {"/"})
    e5 = next(e for e in imp["elements"] if e["eid"] == "E5")
    assert e5["confirmed"] is False and e5["risk"] == 0.3        # 1.0 x 0.6, halved
    assert "S3" not in {s["fp"] for s in imp["screens"]}
    assert "S1" in {s["fp"] for s in imp["screens"]}             # E1 is uniquely linked: A is really there

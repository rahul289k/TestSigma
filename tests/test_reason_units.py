from triangulate.link.gaps import build_gaps
from triangulate.reason.changes import classify_vue, parse_hunks
from triangulate.reason.writer import Note, Question, ReportText, RetestItem, validate

PATCH = """@@ -10,3 +10,4 @@ ctx
 a
-b
+c
+d
@@ -100,0 +101,2 @@
+x
+y
"""


def test_parse_hunks_counts_changed_lines_not_context():
    assert parse_hunks(PATCH) == [(11, 11), (100, 100)]
    assert parse_hunks(None) == []


def test_edit_at_block_boundary_is_not_attributed_to_neighbour_block():
    # template ends at line 50, <script setup> starts at 52; only line 53 changes, with 3 context lines around it
    patch = "@@ -50,7 +50,7 @@\n </template>\n \n <script setup>\n-const a = 1\n+const a = 2\n import x from 'y'\n import z\n const q = 3\n"
    blocks = {"template": [1, 50], "script": None, "scriptSetup": [52, 120], "styles": []}
    assert parse_hunks(patch) == [(53, 53)]
    assert classify_vue(parse_hunks(patch), blocks) == ["script"]


def test_classify_vue_blocks():
    blocks = {"template": [1, 50], "script": None, "scriptSetup": [52, 120], "styles": [[122, 140]]}
    assert classify_vue([(10, 12)], blocks) == ["template"]
    assert classify_vue([(60, 61), (125, 125)], blocks) == ["script", "style"]
    assert classify_vue([(51, 51)], blocks) == ["whole_file"]


def req(i, t):
    return {"id": f"REQ-p-{i}", "statement": f"s{i}", "testability": t, "testability_reason": "r"}


def test_gap_decision_tree():
    reqs = [req(1, "NOT_WEB_UI"), req(2, "NEEDS_LOGIN"), req(3, "NEEDS_LOGIN"), req(4, "UI_OBSERVABLE"),
            req(5, "UI_OBSERVABLE"), req(6, "UI_OBSERVABLE")]
    links = {"REQ-p-2": {"code_components": ["c.vue"]}, "REQ-p-3": {"code_components": []},
             "REQ-p-4": {"ui_verdict": "COVERED", "ui_elements": ["E1"], "code_components": []},
             "REQ-p-5": {"ui_verdict": "NOT_FOUND", "ui_elements": [], "code_components": ["c.vue"]},
             "REQ-p-6": {"ui_verdict": "NOT_FOUND", "ui_elements": [], "code_components": []}}
    gaps, review = build_gaps(reqs, links, {}, 10)
    kinds = {g["req_id"]: g["kind"] for g in gaps}
    assert kinds == {"REQ-p-1": "NOT_UI_TESTABLE", "REQ-p-2": "OUTSIDE_CRAWL_BOUNDARY", "REQ-p-3": "OUTSIDE_BOUNDARY_UNVERIFIED",
                     "REQ-p-5": "UNOBSERVED_IN_UI", "REQ-p-6": "NOT_FOUND_ANYWHERE"}
    assert sorted(r["trigger"] for r in review) == ["boundary_unverified", "not_found_anywhere"]


IMPACT = {"flows": [{"id": "FLOW-1", "band": "High"}, {"id": "FLOW-2", "band": "Low"}], "screens": [{"fp": "S-1"}],
          "requirements": [{"id": "REQ-a"}], "needs_human": [{"id": "RV-1"}]}


def text(**kw):
    base = dict(headline="Sending a request may look different.", what_changed="The response preview changes.",
                retest=[RetestItem(flow_id="FLOW-1", why="The preview may break.")],
                screen_notes=[Note(id="S-1", note="Preview area.")], requirement_notes=[Note(id="REQ-a", note="At risk.")],
                questions=[Question(about=["RV-1"], question="Is this screen still used?")])
    base.update(kw)
    return ReportText(**base)


def test_validator_accepts_clean_text():
    assert validate(text(), IMPACT) == []
    assert validate(text(headline="Retest the request(s) in each tab(s)."), IMPACT) == []
    # "function" as an English verb is not code jargon; a call to a code function is.
    assert validate(text(headline="Check that request actions function correctly."), IMPACT) == []
    assert validate(text(headline="Calls runRequest() on send."), IMPACT) != []


def test_validator_catches_unknown_ids_missing_items_and_jargon():
    errs = validate(text(retest=[RetestItem(flow_id="FLOW-9", why="x")], requirement_notes=[],
                         headline="The HTMLLensRenderer.vue component changed"), IMPACT)
    joined = " | ".join(errs)
    assert "unknown flow FLOW-9" in joined
    assert "High flows missing" in joined
    assert "requirements without a note" in joined
    assert "code jargon" in joined


def test_import_and_comment_lines_are_not_behavioural_changes():
    patch = "@@ -3,3 +3,4 @@\n import a from 'a'\n+import { b } from '~/b'\n // note\n@@ -105,3 +106,3 @@\n   x()\n-  y()\n+  z()\n"
    assert parse_hunks(patch) == [(3, 3), (106, 106)]
    assert parse_hunks(patch, behavioural_only=True) == [(106, 106)]

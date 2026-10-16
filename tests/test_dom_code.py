from triangulate.link.dom_code import KeyIndex, link_elements
from triangulate.link.uidata import UIData

PRIORS = {"route_page": 0.95, "i18n_route_unique": 0.9, "i18n_route_tiebreak": 0.7, "i18n_route_ambiguous": 0.4,
          "i18n_global_unique": 0.5, "i18n_global_ambiguous": 0.25, "llm_judge": 0.8, "human": 1.0}

I18N = {"action.send": "Send", "chat.send": "Send", "tab.headers": "Headers", "tab.body": "Body", "tab.params": "Parameters",
        "share.headers": "Headers", "count.items": "{count} items", "app.search": "Search and commands", "x.login": "Login"}

CODE = {
    "i18n": I18N,
    "components": [
        {"id": "src/components/http/Request.vue", "keys": ["action.send"], "key_patterns": [], "testids": []},
        {"id": "src/components/chat/Box.vue", "keys": ["chat.send"], "key_patterns": [], "testids": []},
        {"id": "src/components/http/Options.vue", "keys": ["tab.headers", "tab.body", "tab.params"], "key_patterns": [], "testids": []},
        {"id": "src/components/share/Big.vue", "keys": ["share.headers", "tab.body", "tab.params", "x.a", "x.b", "x.c", "x.d"],
         "key_patterns": [], "testids": []},
        {"id": "src/components/app/Header.vue", "keys": ["app.search", "x.login"], "key_patterns": [], "testids": []},
    ],
    "routes": [{"path": "/", "reachable": ["src/components/http/Request.vue", "src/components/http/Options.vue",
                                           "src/components/share/Big.vue", "src/components/app/Header.vue"]}],
}


def e(eid, name, x, y, ctx="main", **kw):
    return {"eid": eid, "sig": eid, "name": name, "role": "button", "kind": "action", "context": ctx, "tooltip": "",
            "placeholder": "", "aria_label": "", "title": "", "text": name, "bbox": [x, y, 80, 20], "occ": 0, **kw}


def ui(elements):
    state = {"fp": "S1", "route": "/", "order": 0, "path": [], "elements": elements}
    els = {x["eid"]: {**x, "route": "/", "states": ["S1"]} for x in elements}
    return UIData(states={"S1": state}, transitions=[], elements=els, labels={})


def test_key_index_exact_placeholder_and_trimmed():
    idx = KeyIndex(I18N)
    assert idx.lookup("Send") == (["action.send", "chat.send"], "exact")
    assert idx.lookup("12 items") == (["count.items"], "placeholder")
    assert idx.lookup("Search and commands ⌘ K") == (["app.search"], "trimmed")
    assert idx.lookup("Untitled") == ([], "")


def test_route_tree_resolves_shared_key():
    linked, _ = link_elements(ui([e("E1", "Send", 10, 10)]), CODE, PRIORS)
    assert linked[0]["method"] == "i18n_route_unique"
    assert [c["component"] for c in linked[0]["components"]] == ["src/components/http/Request.vue"]
    assert linked[0]["components"][0]["confidence"] == 0.9


def test_tiebreak_prefers_specific_component_over_big_one():
    els = [e("E1", "Headers", 100, 100), e("E2", "Body", 190, 100), e("E3", "Parameters", 10, 100)]
    linked, _ = link_elements(ui(els), CODE, PRIORS)
    headers = next(r for r in linked if r["eid"] == "E1")
    assert headers["method"] == "i18n_route_tiebreak"
    assert headers["components"][0]["component"] == "src/components/http/Options.vue"


def test_unmapped_text_is_reported_not_guessed():
    linked, unmapped = link_elements(ui([e("E9", "Untitled", 0, 0)]), CODE, PRIORS)
    assert linked == [] and unmapped[0]["eid"] == "E9"

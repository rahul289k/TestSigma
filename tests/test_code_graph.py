from triangulate.code.graph import build_code_graph

I18N = {"navigation.rest": "REST", "lens.json": "JSON", "action.send": "Send"}


def comp(file, tags=(), keys=(), literals=(), dynamic=False, imports=()):
    return {"file": file, "blocks": {"template": [1, 2], "script": None, "scriptSetup": [3, 9], "styles": []},
            "tags": list(tags), "dynamicComponent": dynamic, "testids": [], "keys": list(keys), "keyPatterns": [],
            "keyLiterals": list(literals), "imports": [{"spec": i, "resolved": i, "names": [], "default": None,
                                                         "namespace": True, "sideEffect": False} for i in imports],
            "importBindings": {}, "routeMeta": None}


def ts(file, imports=()):
    return {"file": file, "imports": [{"spec": i, "resolved": i, "names": [], "default": None, "namespace": True,
                                       "sideEffect": False} for i in imports], "symbols": [], "keys": [], "keyPatterns": []}


RAW = {
    "components": [
        comp("src/App.vue"), comp("src/layouts/default.vue", tags=["AppNav"]),
        comp("src/components/app/Nav.vue", literals=["navigation.rest", "not.a.key"]),
        comp("src/pages/index.vue", tags=["Body"]),
        comp("src/components/Body.vue", dynamic=True, imports=["src/helpers/lenses.ts"]),
        comp("src/components/renderers/Json.vue", keys=["lens.json"]),
        comp("src/components/Orphan.vue", keys=["action.send"]),
    ],
    "tsFiles": [ts("src/helpers/lenses.ts", ["src/helpers/jsonLens.ts"]),
                ts("src/helpers/jsonLens.ts", ["src/components/renderers/Json.vue"])],
    "globalComponents": {"AppNav": "src/components/app/Nav.vue", "Body": "src/components/Body.vue"},
    "routes": [{"path": "/", "chain": ["src/pages/index.vue"]}],
    "errors": [],
}


def test_dynamic_renders_are_resolved_through_ts_imports():
    g = build_code_graph(RAW, I18N, ["Hopp"])
    body = next(c for c in g["components"] if c["id"] == "src/components/Body.vue")
    assert body["renders_dynamic"] == ["src/components/renderers/Json.vue"]
    reach = set(g["routes"][0]["reachable"])
    assert "src/components/renderers/Json.vue" in reach      # reachable only through the dynamic edge
    assert "src/components/Orphan.vue" not in reach


def test_literal_i18n_keys_count_as_uses_only_when_the_key_exists():
    g = build_code_graph(RAW, I18N, ["Hopp"])
    nav = next(c for c in g["components"] if c["id"] == "src/components/app/Nav.vue")
    assert nav["keys"] == ["navigation.rest"] and nav["literal_keys"] == ["navigation.rest"]


def test_repeated_imports_of_one_file_keep_all_names():
    raw = {**RAW, "components": RAW["components"] + [
        {**comp("src/components/Two.vue"), "imports": [
            {"spec": "src/helpers/lenses.ts", "resolved": "src/helpers/lenses.ts", "names": ["a"], "default": None,
             "namespace": False, "sideEffect": False},
            {"spec": "src/helpers/lenses.ts", "resolved": "src/helpers/lenses.ts", "names": ["b"], "default": None,
             "namespace": False, "sideEffect": False}]}]}
    g = build_code_graph(raw, I18N, ["Hopp"])
    two = next(f for f in g["files"] if f["path"] == "src/components/Two.vue")
    assert two["imports"] == [{"target": "src/helpers/lenses.ts", "names": ["a", "b"], "default": None,
                               "namespace": False, "side_effect": False}]


def test_dynamic_targets_use_shortest_import_path():
    # Host -> a.ts -> d.ts -> Target.vue (2 TS hops) and Host -> b.ts -> c.ts -> d.ts (3 hops): Target must be found.
    raw = {"components": [comp("src/App.vue"), comp("src/layouts/default.vue"),
                          comp("src/pages/index.vue", tags=["Host"]),
                          comp("src/components/Host.vue", dynamic=True, imports=["src/b.ts", "src/a.ts"]),
                          comp("src/components/Target.vue")],
           "tsFiles": [ts("src/a.ts", ["src/d.ts"]), ts("src/b.ts", ["src/c.ts"]), ts("src/c.ts", ["src/d.ts"]),
                       ts("src/d.ts", ["src/components/Target.vue"])],
           "globalComponents": {"Host": "src/components/Host.vue"},
           "routes": [{"path": "/", "chain": ["src/pages/index.vue"]}], "errors": []}
    g = build_code_graph(raw, I18N, ["Hopp"])
    host = next(c for c in g["components"] if c["id"] == "src/components/Host.vue")
    assert host["renders_dynamic"] == ["src/components/Target.vue"]

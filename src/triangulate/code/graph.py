"""Stage 3: code graph.

Runs the Node extractor (real Vue + TypeScript compilers) over the pinned package and turns
its output into the code layer: components with block line ranges, render edges resolved
through the build's own component map (components.d.ts), import edges, exported symbols,
i18n usage, and per-route render trees. Fully deterministic.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import Config
from ..runstore import RunStore

NODE_DIR = Path(__file__).parent / "node"


class CodeGraphError(RuntimeError):
    pass


def flatten_i18n(obj: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten_i18n(v, key + "."))
        elif isinstance(v, str):
            out[key] = v
    return out


def run_extractor(package_root: Path, aliases: dict[str, str], out: Path, files: list[str] | None = None) -> dict:
    if not (NODE_DIR / "node_modules").exists():
        raise CodeGraphError(f"Node dependencies missing. Run: npm install --prefix {NODE_DIR}")
    cmd = ["node", str(NODE_DIR / "extract.mjs"), "--root", str(package_root), "--out", str(out),
           "--aliases", json.dumps(aliases)]
    if files:
        cmd += ["--files", ",".join(files)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise CodeGraphError(f"extractor failed: {res.stderr.strip()[:800]}")
    return json.loads(out.read_text())


def component_kind(file: str) -> str:
    if file.startswith("src/pages/"):
        return "page"
    if file.startswith("src/layouts/"):
        return "layout"
    if file == "src/App.vue":
        return "app"
    return "component"


def build_code_graph(raw: dict, i18n: dict[str, str], external_prefixes: list[str]) -> dict:
    global_map: dict[str, str] = raw["globalComponents"]
    file_to_name = {v: k for k, v in global_map.items() if not v.startswith("external:")}
    comp_files = {c["file"] for c in raw["components"]}

    components = []
    unresolved_tags: dict[str, int] = {}
    for c in raw["components"]:
        renders, external = set(), set()
        for tag in c["tags"]:
            target = c["importBindings"].get(tag) or global_map.get(tag)
            if target and not target.startswith("external:") and target in comp_files:
                renders.add(target)
            elif target or any(tag.startswith(p) for p in external_prefixes):
                external.add(tag)
            else:
                unresolved_tags[tag] = unresolved_tags.get(tag, 0) + 1
                external.add(tag)
        known = [k for k in c["keys"] if k in i18n]
        literal = sorted({k for k in c.get("keyLiterals", []) if k in i18n} - set(known))
        name = file_to_name.get(c["file"]) or c["file"].removeprefix("src/").removesuffix(".vue").replace("/", "_")
        components.append({
            "id": c["file"], "name": name, "kind": component_kind(c["file"]), "blocks": c["blocks"],
            "keys": sorted(set(known) | set(literal)), "literal_keys": literal,
            "unknown_keys": sorted(set(c["keys"]) - set(known)),
            "key_patterns": c["keyPatterns"], "testids": c["testids"], "renders": sorted(renders),
            "external_renders": sorted(external), "dynamic_component": c["dynamicComponent"],
            "layout": (c.get("routeMeta") or {}).get("layout"),
        })

    def merged_imports(imports: list[dict]) -> list[dict]:
        """One entry per imported file: two import statements from the same file must not lose names."""
        by_target: dict[str, dict] = {}
        for i in imports:
            if not i["resolved"]:
                continue
            m = by_target.setdefault(i["resolved"], {"target": i["resolved"], "names": [], "default": None,
                                                     "namespace": False, "side_effect": False})
            m["names"] = sorted(set(m["names"]) | set(i["names"]))
            m["default"] = m["default"] or i["default"]
            m["namespace"] = m["namespace"] or i["namespace"]
            m["side_effect"] = m["side_effect"] or i["sideEffect"]
        return [by_target[t] for t in sorted(by_target)]

    files = []
    for item in raw["components"] + raw["tsFiles"]:
        files.append({
            "path": item["file"], "lang": "vue" if item["file"].endswith(".vue") else "ts",
            "imports": merged_imports(item["imports"]),
            "external_imports": sorted({i["spec"] for i in item["imports"] if not i["resolved"]}),
            "symbols": item.get("symbols", []),
            "keys": sorted(k for k in item.get("keys", []) if k in i18n),
        })

    by_id = {c["id"]: c for c in components}
    files_by_path = {f["path"]: f for f in files}

    def dynamic_targets(cid: str) -> set[str]:
        """Components a <component :is> user can render: .vue files its script reaches through
        imports (static or lazy import()) of at most two TypeScript modules."""
        out: set[str] = set()
        seen = {cid}
        frontier = [(cid, 0)]
        while frontier:
            path, depth = frontier.pop(0)   # breadth-first: each module is expanded at its shortest depth
            for imp in files_by_path.get(path, {}).get("imports", []):
                t = imp["target"]
                if t in seen:
                    continue
                seen.add(t)
                if t.endswith(".vue"):
                    out.add(t)
                elif depth < 2:
                    frontier.append((t, depth + 1))
        return out

    parents: dict[str, list[str]] = {}
    for c in components:
        for child in c["renders"]:
            parents.setdefault(child, []).append(c["id"])
    for c in components:
        if not c["dynamic_component"]:
            c["renders_dynamic"] = []
            continue
        # What a <component :is> can show: components its own script reaches, plus components its parents
        # reach (they are often passed in as props, e.g. import steps or lens renderers).
        targets = dynamic_targets(c["id"])
        for p in parents.get(c["id"], []):
            targets |= dynamic_targets(p)
        c["renders_dynamic"] = sorted(targets - set(c["renders"]) - {c["id"]})

    def closure(roots: list[str]) -> list[str]:
        seen, stack = set(), [r for r in roots if r in by_id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(by_id[cur]["renders"])
            stack.extend(by_id[cur]["renders_dynamic"])
        return sorted(seen)

    routes = []
    for r in raw["routes"]:
        leaf = by_id.get(r["chain"][-1])
        layout_name = next((by_id[f]["layout"] for f in reversed(r["chain"]) if f in by_id and by_id[f]["layout"]), None)
        layout = f"src/layouts/{layout_name or 'default'}.vue"
        roots = ["src/App.vue", layout, *r["chain"]]
        routes.append({"path": r["path"], "chain": r["chain"], "layout": layout,
                       "reachable": closure(roots), "leaf": leaf["id"] if leaf else None})

    stats = {
        "components": len(components), "ts_files": len(raw["tsFiles"]), "routes": len(routes),
        "render_edges": sum(len(c["renders"]) for c in components),
        "dynamic_render_edges": sum(len(c["renders_dynamic"]) for c in components),
        "import_edges": sum(len(f["imports"]) for f in files),
        "components_using_keys": sum(1 for c in components if c["keys"]),
        "unresolved_tags": dict(sorted(unresolved_tags.items())),
        "dynamic_component_users": sorted(c["id"] for c in components if c["dynamic_component"]),
        "extractor_errors": raw["errors"],
        "i18n_keys": len(i18n),
    }
    return {"components": components, "files": files, "routes": routes, "i18n": i18n, "stats": stats}


def run_code_graph(cfg: Config, run: RunStore) -> dict:
    code_path = Path(run.manifest["code"]["path"])
    pkg = code_path / cfg.code.common_root
    raw = run_extractor(pkg, cfg.code.aliases, run.path("code/extractor_raw.json"))
    if raw["errors"]:
        raise CodeGraphError(f"{len(raw['errors'])} files failed to parse, e.g. {raw['errors'][0]}")
    i18n = flatten_i18n(json.loads((pkg / cfg.code.locale_file).read_text()))
    graph = build_code_graph(raw, i18n, cfg.code.external_tag_prefixes)
    run.path("code/extractor_raw.json").unlink()
    run.write_json("code/code_graph.json", graph)
    return graph["stats"]

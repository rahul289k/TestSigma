// Code-graph extractor for a Vue 3 + TypeScript codebase.
//
// Uses the real compilers (@vue/compiler-sfc for .vue, the TypeScript compiler API for
// scripts) so that templates, directives and imports are read the way the build reads them.
//
// Usage:
//   node extract.mjs --root <package root> --out <file.json> [--aliases <json>]
//   node extract.mjs --files <a.vue,b.ts> --root <package root> --out <file.json>   (single-file mode, for PR diffs)
//
// Output is deterministic: every list is sorted.

import fs from "node:fs"
import path from "node:path"
import { parse as parseSfc } from "@vue/compiler-sfc"
import ts from "typescript"

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => (a.startsWith("--") ? [...acc, [a.slice(2), arr[i + 1]]] : acc), [])
)
const ROOT = path.resolve(args.root)
const SRC = path.join(ROOT, "src")
const ALIASES = JSON.parse(args.aliases || "{}") // { "~": "src", "@helpers": "src/helpers", ... }
const errors = []

const rel = (p) => path.relative(ROOT, p).split(path.sep).join("/")
const sortU = (xs) => [...new Set(xs)].sort()

// ---------------------------------------------------------------- i18n keys used in source text
const KEY_STATIC = /\bt\(\s*(['"`])([A-Za-z0-9_.\-]+)\1/g
const KEY_DYNAMIC = /\bt\(\s*`([A-Za-z0-9_.\-]*)\$\{/g
// String literals shaped like i18n keys ("navigation.rest") - later kept only if the key exists.
// They catch keys that are passed around as data and rendered with t(variable).
const KEY_LITERAL = /(['"`])([a-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+)+)\1/g
function i18nKeys(text) {
  const keys = []
  const patterns = []
  const literals = []
  for (const m of text.matchAll(KEY_STATIC)) keys.push(m[2])
  for (const m of text.matchAll(KEY_DYNAMIC)) if (m[1]) patterns.push(m[1])
  for (const m of text.matchAll(KEY_LITERAL)) literals.push(m[2])
  return { keys: sortU(keys), patterns: sortU(patterns), literals: sortU(literals) }
}

// ---------------------------------------------------------------- import resolution
const EXTS = ["", ".ts", ".vue", ".js", ".mjs", "/index.ts", "/index.vue", "/index.js"]
function resolveSpecifier(rawSpec, fromFile) {
  const spec = rawSpec.split("?")[0]
  let base = null
  if (spec === "." || spec === ".." || spec.startsWith("./") || spec.startsWith("../")) base = path.resolve(path.dirname(fromFile), spec)
  else {
    for (const [alias, target] of Object.entries(ALIASES).sort((a, b) => b[0].length - a[0].length)) {
      if (spec === alias || spec.startsWith(alias + "/")) {
        base = path.join(ROOT, target, spec.slice(alias.length))
        break
      }
    }
  }
  if (!base) return null
  const bases = base.endsWith(".js") ? [base, base.slice(0, -3)] : [base]
  for (const b of bases) {
    for (const ext of EXTS) {
      const cand = b + ext
      if (fs.existsSync(cand) && fs.statSync(cand).isFile()) return rel(cand)
    }
  }
  return null
}

function scriptInfo(code, fileAbs, lineOffset = 0) {
  const sf = ts.createSourceFile(fileAbs, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const imports = []
  const symbols = []
  const line = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1 + lineOffset
  const addImport = (spec, names, isDefault, ns, sideEffect) => {
    const resolved = resolveSpecifier(spec, fileAbs)
    imports.push({ spec, resolved, names: sortU(names), default: isDefault || null, namespace: !!ns, sideEffect: !!sideEffect })
  }
  const visit = (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const clause = node.importClause
      const names = []
      let def = null
      let ns = false
      if (clause) {
        if (clause.name) def = clause.name.text
        const nb = clause.namedBindings
        if (nb && ts.isNamespaceImport(nb)) ns = true
        if (nb && ts.isNamedImports(nb)) for (const el of nb.elements) names.push((el.propertyName || el.name).text)
      }
      addImport(node.moduleSpecifier.text, names, def, ns, !clause)
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      const names = node.exportClause && ts.isNamedExports(node.exportClause)
        ? node.exportClause.elements.map((e) => (e.propertyName || e.name).text) : []
      addImport(node.moduleSpecifier.text, names, null, !node.exportClause, false)
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword &&
               node.arguments.length && ts.isStringLiteral(node.arguments[0])) {
      addImport(node.arguments[0].text, [], null, true, false)
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  for (const st of sf.statements) {
    const exported = !!(ts.getCombinedModifierFlags(st) & ts.ModifierFlags.Export)
    const push = (name, kind, n) => symbols.push({ name, kind, exported, start: line(n.getStart()), end: line(n.getEnd()) })
    if (ts.isFunctionDeclaration(st) && st.name) push(st.name.text, "function", st)
    else if (ts.isClassDeclaration(st) && st.name) {
      push(st.name.text, "class", st)
      for (const m of st.members) {
        if ((ts.isMethodDeclaration(m) || ts.isPropertyDeclaration(m) || ts.isGetAccessor(m)) && m.name && ts.isIdentifier(m.name))
          symbols.push({ name: `${st.name.text}.${m.name.text}`, kind: "member", exported, start: line(m.getStart()), end: line(m.getEnd()) })
      }
    } else if (ts.isVariableStatement(st)) {
      for (const d of st.declarationList.declarations) if (ts.isIdentifier(d.name)) push(d.name.text, "variable", st)
    } else if (ts.isInterfaceDeclaration(st) || ts.isTypeAliasDeclaration(st)) push(st.name.text, "type", st)
    else if (ts.isEnumDeclaration(st)) push(st.name.text, "enum", st)
  }
  return { imports, symbols }
}

// ---------------------------------------------------------------- .vue files
const kebabToPascal = (s) => s.split("-").map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join("")
function vueInfo(fileAbs) {
  const source = fs.readFileSync(fileAbs, "utf8")
  const { descriptor, errors: errs } = parseSfc(source, { filename: fileAbs })
  if (errs.length) errors.push({ file: rel(fileAbs), error: String(errs[0].message || errs[0]) })
  const span = (b) => (b ? [b.loc.start.line, b.loc.end.line] : null)
  const tags = []
  const testids = []
  let dynamicComponent = false
  const walk = (node) => {
    if (!node) return
    if (node.type === 1) {
      if (node.tagType === 1 || /[A-Z]/.test(node.tag[0]) || node.tag.includes("-")) {
        if (node.tag === "component") dynamicComponent = true
        else tags.push(kebabToPascal(node.tag))
      }
      for (const p of node.props || []) if (p.type === 6 && p.name === "data-testid" && p.value) testids.push(p.value.content)
    }
    for (const c of node.children || []) walk(c)
    if (node.branches) for (const b of node.branches) walk(b)
  }
  if (descriptor.template && descriptor.template.ast) walk(descriptor.template.ast)
  const scripts = [descriptor.script, descriptor.scriptSetup].filter(Boolean)
  let imports = []
  for (const s of scripts) {
    try {
      imports = imports.concat(scriptInfo(s.content, fileAbs, s.loc.start.line - 1).imports)
    } catch (e) {
      errors.push({ file: rel(fileAbs), error: `script parse: ${e.message}` })
    }
  }
  const importBindings = {}
  for (const im of imports) if (im.default && im.resolved && im.resolved.endsWith(".vue")) importBindings[im.default] = im.resolved
  const text = (descriptor.template ? descriptor.template.content : "") + "\n" + scripts.map((s) => s.content).join("\n")
  const { keys, patterns, literals } = i18nKeys(text)
  let routeMeta = null
  for (const cb of descriptor.customBlocks) {
    if (cb.type === "route") {
      const m = cb.content.match(/layout["']?\s*:\s*["']?([A-Za-z0-9_-]+)/)
      routeMeta = { layout: m ? m[1] : null }
    }
  }
  return {
    file: rel(fileAbs),
    blocks: { template: span(descriptor.template), script: span(descriptor.script), scriptSetup: span(descriptor.scriptSetup),
              styles: descriptor.styles.map((s) => [s.loc.start.line, s.loc.end.line]) },
    tags: sortU(tags), dynamicComponent, testids: sortU(testids), keys, keyPatterns: patterns, keyLiterals: literals,
    imports: imports.sort((a, b) => (a.spec < b.spec ? -1 : 1)), importBindings, routeMeta,
  }
}

function tsInfo(fileAbs) {
  const code = fs.readFileSync(fileAbs, "utf8")
  const { imports, symbols } = scriptInfo(code, fileAbs)
  const { keys, patterns } = i18nKeys(code)
  return { file: rel(fileAbs), imports: imports.sort((a, b) => (a.spec < b.spec ? -1 : 1)), symbols, keys, keyPatterns: patterns }
}

// ---------------------------------------------------------------- global component map (components.d.ts)
function globalComponents() {
  const dts = path.join(SRC, "components.d.ts")
  const map = {}
  if (!fs.existsSync(dts)) return map
  const re = /^\s*([A-Za-z0-9_]+): typeof import\('([^']+)'\)/gm
  for (const m of fs.readFileSync(dts, "utf8").matchAll(re)) {
    const target = m[2].startsWith("./") ? rel(path.join(SRC, m[2])) : `external:${m[2]}`
    map[m[1]] = target
  }
  return map
}

// ---------------------------------------------------------------- routes (vite-plugin-pages, nuxt style)
function routes(pagesDir) {
  const files = []
  const walkDir = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
      const p = path.join(d, e.name)
      if (e.isDirectory()) walkDir(p)
      else if (e.name.endsWith(".vue")) files.push(path.relative(pagesDir, p).split(path.sep).join("/"))
    }
  }
  if (!fs.existsSync(pagesDir)) return []
  walkDir(pagesDir)
  const seg = (s) => (s === "index" ? "" : s === "_" ? ":all(.*)*" : s.startsWith("_") ? ":" + s.slice(1) : s)
  const out = []
  for (const f of files) {
    const parts = f.replace(/\.vue$/, "").split("/")
    // Skip a parent file (X.vue next to dir X/) as a standalone route only if it has an index child.
    const chain = []
    for (let i = 1; i < parts.length; i++) {
      const parent = parts.slice(0, i).join("/") + ".vue"
      if (files.includes(parent)) chain.push(parent)
    }
    chain.push(f)
    const isParent = files.some((g) => g.startsWith(f.replace(/\.vue$/, "") + "/"))
    if (isParent) continue
    const p = "/" + parts.map(seg).filter((x) => x !== "").join("/")
    out.push({ path: p === "/" ? "/" : p.replace(/\/$/, ""), chain: chain.map((c) => rel(path.join(pagesDir, c))) })
  }
  return out.sort((a, b) => (a.path < b.path ? -1 : 1))
}

// ---------------------------------------------------------------- main
function listFiles(dir, exts) {
  const out = []
  const walkDir = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      if (e.name === "node_modules" || e.name === "__tests__" || e.name.startsWith(".")) continue
      const p = path.join(d, e.name)
      if (e.isDirectory()) walkDir(p)
      else if (exts.some((x) => e.name.endsWith(x)) && !/\.(spec|test)\.ts$/.test(e.name) && !e.name.endsWith(".d.ts")) out.push(p)
    }
  }
  walkDir(dir)
  return out.sort()
}

const result = { root: ROOT, components: [], tsFiles: [], globalComponents: {}, i18n: {}, routes: [], errors }
const only = args.files ? args.files.split(",").map((f) => path.resolve(ROOT, f)) : null
const vueFiles = only ? only.filter((f) => f.endsWith(".vue")) : listFiles(SRC, [".vue"])
const tsFiles = only ? only.filter((f) => f.endsWith(".ts")) : listFiles(SRC, [".ts"])
for (const f of vueFiles) {
  try { result.components.push(vueInfo(f)) } catch (e) { errors.push({ file: rel(f), error: e.message }) }
}
for (const f of tsFiles) {
  try { result.tsFiles.push(tsInfo(f)) } catch (e) { errors.push({ file: rel(f), error: e.message }) }
}
if (!only) {
  result.globalComponents = globalComponents()
  result.routes = routes(path.join(SRC, "pages"))
}
fs.writeFileSync(args.out, JSON.stringify(result, null, 1))
console.log(JSON.stringify({ components: result.components.length, tsFiles: result.tsFiles.length, routes: result.routes.length,
  globalComponents: Object.keys(result.globalComponents).length, errors: errors.length }))

# What the run numbers mean

Every stage prints one line of numbers when it finishes, for example:

```
▶ ingest
  {"pages": 26, "sections": 315, "proposed": 139, "kept": 116, "rejected": 23, ...}
```

The same numbers are saved in each run's manifest, so you can read them again without re-running anything:

```bash
jq '.stage_stats' runs/<run_id>/manifest.json
```

This page explains every key. The values are from the real run `20261016T065647Z-default` (Hoppscotch 2026.9.0).
Every stage also reports **`seconds`**, which is simply how long it took.

Contents: [pin](#pin) · [ingest](#ingest) · [code](#code) · [crawl](#crawl) · [label](#label) · [load](#load) ·
[domcode](#domcode) · [judge](#judge) · [probe](#probe) · [probe_post](#probe_post) · [gaps](#gaps) ·
[flows](#flows) · [review](#review) · [final_load](#final_load) · [eval scorecard](#eval-scorecard)

---

## pin

Reads which release is live and downloads exactly that code and the docs.

| Key | Value | Meaning |
|---|---|---|
| `app.version` | 2026.9.0 | the release running on hoppscotch.io, read from the app's own JavaScript |
| `code.tag` | 2026.9.0 | the git tag of that release |
| `code.sha` | 63273f8… | the exact code commit downloaded |
| `docs.sha` | 55a3ac5… | the exact docs commit downloaded |
| `code.path`, `docs.path` | `data/repos/…` | where the local copies are |

## ingest

Docs → requirements. The LLM proposes requirements; code keeps only those whose quote is really in the docs.

| Key | Value | Meaning |
|---|---|---|
| `pages` | 26 | documentation pages read |
| `sections` | 315 | pieces the pages were cut into, one per heading |
| `proposed` | 139 | requirements the LLM suggested |
| `kept` | 116 | passed the check: the quote appears word for word in its section |
| `rejected` | 23 | failed the check (quote not found, too short, duplicate or unknown section). 139 − 23 = 116 |
| `by_testability.UI_OBSERVABLE` | 102 | can be seen in the web app without signing in |
| `by_testability.NEEDS_LOGIN` | 12 | needs an account (teams, sharing, sync) |
| `by_testability.NOT_WEB_UI` | 2 | not part of the web app (CLI, self-hosting, server) |

Details: `runs/<id>/ingest/requirements.jsonl` and `rejected.jsonl` (each rejection has a `reason`).

## code

Reads the pinned code with the real Vue and TypeScript compilers.

| Key | Value | Meaning |
|---|---|---|
| `components` | 335 | Vue components (`.vue` files) |
| `ts_files` | 421 | TypeScript files |
| `routes` | 18 | URL pages the app defines |
| `render_edges` | 463 | "component A draws component B", read from templates |
| `dynamic_render_edges` | 135 | the same, for components chosen at run time (`<component :is>`) |
| `import_edges` | 2,780 | "file A imports file B" |
| `i18n_keys` | 2,198 | labels in the translation file (`en.json`) |
| `components_using_keys` | 267 | components that show at least one translated label |

## crawl

The robot browses the live app with a real (invisible) browser.

| Key | Value | Meaning |
|---|---|---|
| `states` | 243 | distinct screens found |
| `transitions` | 524 | recorded clicks and key presses, including ones that led back to a known screen |
| `minutes` | 20.3 | crawl time |
| `resets` | 197 | times a fresh browser was opened to replay the way back to a screen |
| `replay_mismatches` | 0 | replays that did not land on the expected screen |
| `failed_actions` | 0 | clicks that errored |
| `blocked_navigations` | 0 | attempts to leave hoppscotch.io, which are blocked |
| `popups_closed` | 0 | pop-up windows closed |
| `load_retries` | 0 | page loads that had to be retried |
| `app_version` | 2026.9.0 | version read again at the end; it must equal the pinned one, or the run is invalid |

`per_seed` has one entry per start page (7 pages). For example `/settings`:

| Key | Value | Meaning |
|---|---|---|
| `states` | 35 | screens that belong to this start page |
| `new_states` | 34 | screens first discovered during this page's turn (its first screen was already reached from another page) |
| `actions` | 77 | clicks made from this page |
| `stopped` | explored | why exploration of this page ended |

Values of `stopped`:

| Value | Meaning |
|---|---|
| `explored` | nothing new was left to click |
| `route_cap` | reached the limit of 60 screens for this page (`/` and `/graphql`) |
| `max_states` | reached the total limit of 250 screens |
| `time` | cut short by the time limit; such a run is not comparable with another run |

Details: `runs/<id>/crawl/states.jsonl`, `transitions.jsonl`, `screens/*.jpg`, `meta.json`.

## label

| Key | Value | Meaning |
|---|---|---|
| `labeled` | 243 | screens given a readable name by the LLM (names are for people only, never used as evidence) |

## load

Puts the three layers into Neo4j, with no links between them yet.

| Key | Value | Meaning |
|---|---|---|
| `requirements` | 116 | requirement nodes |
| `states` | 243 | screen nodes |
| `elements` | 1,044 | control nodes (a button that appears on many screens is one node) |
| `components` | 335 | component nodes |

## domcode

Links each control on screen to the component that draws it (visible text → translation key → component). No LLM.

| Key | Value | Meaning |
|---|---|---|
| `elements` | 1,044 | distinct controls considered |
| `mapped` | 733 | linked to a component |
| `unmapped` | 311 | not linked; each has a reason in `dom_code_unmapped.jsonl` |
| `mapped_share` | 0.702 | 733 ÷ 1,044 |
| `overrides_applied` | 0 | human decisions from `config/overrides.yaml` used |

`by_method` says how each link was decided:

| Method | Count | Meaning |
|---|---|---|
| `i18n_route_unique` | 298 | exactly one component that can be on this page uses the text: certain |
| `i18n_route_tiebreak` | 331 | several could; one won because it also draws the neighbouring controls |
| `i18n_route_ambiguous` | 65 | still tied; all tied components are linked and the confidence is split between them |
| `i18n_global_unique` | 17 | no candidate on this page; one component anywhere in the code uses the text (weak) |
| `i18n_global_ambiguous` | 22 | no candidate on this page; several anywhere (weakest) |

## judge

The LLM decides, for each requirement, which of the retrieved controls and components implement it. Code checks
every id it cites.

| Key | Value | Meaning |
|---|---|---|
| `judged` | 114 | requirements judged (116 minus the 2 that are not web UI) |
| `ui_verdicts.COVERED` | 51 | a control clearly does it |
| `ui_verdicts.PARTIAL` | 32 | a control shows part of it (for example the entry point) |
| `ui_verdicts.NOT_FOUND` | 19 | no retrieved control does it |
| `ui_verdicts.NOT_APPLICABLE` | 12 | needs sign-in, so the UI part was not judged |
| `code_present` | 114 | the judge said a code component implements it (every one; a known weakness of the lite model) |
| `review_items` | 6 | questions raised for a person |
| `overrides_applied` | 0 | human decisions used |

## probe

The LLM agent searches the live app for requirements the crawl did not find.

| Key | Value | Meaning |
|---|---|---|
| `episodes` | 1 | missing requirements searched for (capped at 1 by the free API quota) |
| `decision_calls` | 1 | decisions the agent made |
| `found` | 0 | requirements it found |
| `new_states` | 0 | new screens it reached |
| `transitions` | 0 | clicks it recorded |

## probe_post

Processes what the probe found.

| Key | Value | Meaning |
|---|---|---|
| `probe_states_labeled` | 0 | new screens named |
| `domcode_mapped` | 733 | controls linked to code after re-linking |
| `rejudged` | 1 | requirements judged again |
| `ui_verdicts` | 51 / 32 / 19 / 12 | verdicts after the re-judge (unchanged here) |

## gaps

Gives every uncovered requirement exactly one reason. No LLM.

| Key | Value | Meaning |
|---|---|---|
| `requirements` | 116 | all requirements |
| `covered` | 83 | COVERED or PARTIAL with at least one valid control |
| `ui_testable` | 102 | requirements visible without signing in |
| `ui_coverage` | 0.814 | 83 ÷ 102 |
| `gaps.UNOBSERVED_IN_UI` | 19 | built in the code, but the crawl never reached it |
| `gaps.OUTSIDE_CRAWL_BOUNDARY` | 12 | needs sign-in, and the code for it exists |
| `gaps.NOT_UI_TESTABLE` | 2 | not part of the web app |

Two more kinds exist but did not occur in this run: `OUTSIDE_BOUNDARY_UNVERIFIED` (needs sign-in and no code was
found) and `NOT_FOUND_ANYWHERE` (no trace in the UI or the code). Both always create a question for a person.

## flows

User journeys: the recorded click path to each covered requirement, replayed in a fresh browser.

| Key | Value | Meaning |
|---|---|---|
| `planned` | 112 | journeys built from recorded click paths |
| `verified` | 112 | replayed successfully, and the target control was visible at the end |
| `failed_replay` | 0 | journeys that did not replay (kept in `flows_failed.jsonl`) |

## review

Questions for a person, collected from the judge and the gaps.

| Key | Value | Meaning |
|---|---|---|
| `open_items` | 5 | questions waiting |
| `by_trigger.judge_no_evidence` | 4 | the judge said "covered" but cited no valid control |
| `by_trigger.judge_invalid_citation` | 1 | the judge cited a control it was not offered |

Why 5 and not the judge's 6: the sixth question was about the one requirement the probe searched for. After the
probe, that requirement was judged again, which replaced its old question.

## final_load

What ended up in Neo4j at the end of the run.

| Key | Value | Meaning |
|---|---|---|
| `requirements`, `states`, `elements`, `components` | 116, 243, 1,044, 335 | the nodes of the three layers |
| `dom_code` | 733 | control → component links written |
| `req_links` | 114 | requirement link rows written (to controls and to components) |
| `gaps` | 33 | gap nodes |
| `flows` | 112 | user-journey nodes |
| `review_items` | 5 | open questions |

---

## eval scorecard

`triangulate eval` prints a scorecard and ends with "run is correct" or the list of what failed. The results are in
`runs/<id>/eval/results.json`.

| Key | Value | Meaning |
|---|---|---|
| `invariants` | 10 of 10 ok | automatic checks that must always hold (quotes verbatim, every requirement covered or exactly one gap, every journey replayed, …) |
| `dom_code.precision_by_method.i18n_route_unique` | 1.00 (11 items) | share of hand-checked "certain" links that were right |
| `dom_code.precision_overall` | 0.691 (34 items) | share of all hand-checked control → code links that were right |
| `req_ui.precision` | 0.842 | of the requirements the system called covered, the share that really are |
| `req_ui.recall` | 0.762 | of the requirements that really are covered, the share the system found |
| `req_ui.absence_precision` | 0.0 | of the requirements the system called missing, the share that really are missing. 0.0 because all 5 it called missing in the gold set are real features the crawl never reached, while the 3 truly missing (sign-in-only AI) features were judged covered |
| `prs[].false_alarms` | none | controls flagged High/Medium that a person marked as clearly unaffected |
| `prs[].recall` | 0.6 for PR 6706 | share of the expected controls the report flagged (3 of 5) |
| `repeatability.matches` | all true | stage hashes identical to the compared run |
| `calibration_measured` | 0.923 / 0.588 / 0.312 | measured confidence per linking method; adopted only with `--update-calibration` |
| `verdict.correct` | true | all invariants pass and every score meets `eval/thresholds.yaml` |

from triangulate.ingest.docs import clean_mdx, normalize, split_sections
from triangulate.ingest.extract import ExtractedRequirement, requirement_id, verify

MDX = '''---
title: Environments
---

{/* hidden comment */}
import Foo from "bar"

An environment groups variables.

<Tabs>
    <Tab title="x">
    ## Creating an environment

    Click on the "Environments" icon on the sidebar to create environments.
    </Tab>
</Tabs>

```js
pw.env.set("a", "b")
```

<Tip>Secret values are **never** exported.</Tip>
'''


def test_clean_mdx_strips_markup_and_dedents():
    meta, text = clean_mdx(MDX)
    assert meta["title"] == "Environments"
    assert "hidden comment" not in text and "import Foo" not in text and "pw.env.set" not in text
    assert "<Tip>" not in text and "Secret values are **never** exported." in text
    assert "\n## Creating an environment" in text  # indentation removed so it is a heading again


def test_sections_have_stable_ids_and_urls():
    _, text = clean_mdx(MDX)
    secs = split_sections("environments", "Environments", text, "https://docs.example.io")
    ids = [s.id for s in secs]
    assert ids == ["environments#intro", "environments#creating-an-environment"]
    assert secs[1].url.endswith("/documentation/features/environments#creating-an-environment")
    assert secs[1].heading_path == ["Environments", "Creating an environment"]


def test_normalize_is_markup_and_quote_insensitive():
    assert normalize('Click the **"History"** icon') == normalize("Click the History icon")
    assert normalize("A\n\n  b") == "A b"


def _req(section_id, quote, statement="User can create an environment"):
    return ExtractedRequirement(section_id=section_id, statement=statement, quote=quote, testability="UI_OBSERVABLE",
                                testability_reason="visible", ui_cues=["Environments"])


def test_verify_keeps_verbatim_and_rejects_paraphrase():
    sec = {"id": "env#c", "page": "env", "page_title": "Env", "heading_path": ["Env"], "url": "u",
           "text": 'Click on the "Environments" icon on the sidebar to create environments.'}
    kept, rejected = verify([_req("env#c", "Click on the Environments icon on the sidebar"),
                             _req("env#c", "Press the environments button in the sidebar", "User can open environments"),
                             _req("nope", "anything at all here")], {"env#c": sec})
    assert len(kept) == 1 and kept[0]["id"].startswith("REQ-env-")
    reasons = sorted(r["reason"] for r in rejected)
    assert reasons == ["quote not found verbatim in section", "unknown section_id"]


def test_requirement_id_is_stable():
    assert requirement_id("p", "User can X", "some quote here") == requirement_id("p", "user can x", "some  quote here")


def test_batch_schema_requires_every_page_and_valid_sections():
    from triangulate.ingest.extract import batch_model
    from triangulate.llm.schema import to_gemini_schema
    secs = [{"id": "env#intro", "page": "env"}, {"id": "env#create", "page": "env"}, {"id": "rest#intro", "page": "rest"}]
    model, names = batch_model(secs)
    g = to_gemini_schema(model)
    assert sorted(g["required"]) == ["page_env", "page_rest"]
    env = g["properties"]["page_env"]
    assert env["minItems"] == 1
    assert env["items"]["properties"]["section_id"]["enum"] == ["env#intro", "env#create"]
    assert names == {"page_env": "env", "page_rest": "rest"}

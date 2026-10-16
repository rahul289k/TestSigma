from triangulate.crawl.model import element_signature, enrich, fingerprint, is_actionable, norm_name

DENY = ["login", "delete"]


def el(**kw):
    base = {"kind": "action", "role": "button", "name": "Send", "context": "main", "selected": False, "expanded": False,
            "checked": False, "disabled": False, "external": False, "target_blank": False, "tooltip": "", "text": ""}
    base.update(kw)
    return base


def test_norm_name_masks_numbers_and_shortcut_glyphs():
    assert norm_name("Search and commands ⌘ K") == "search and commands k"
    assert norm_name("200 OK • 45 ms") == "# ok • # ms"
    assert norm_name("  Add   new ") == "add new"


def test_fingerprint_ignores_order_and_numbers_but_not_selection():
    a = [el(name="Parameters", selected=True), el(name="Headers"), el(name="Status 200")]
    b = [el(name="Status 404"), el(name="Headers"), el(name="Parameters", selected=True)]
    assert fingerprint("/", a) == fingerprint("/", b)
    c = [el(name="Parameters"), el(name="Headers", selected=True), el(name="Status 200")]
    assert fingerprint("/", a) != fingerprint("/", c)
    assert fingerprint("/", a) != fingerprint("/graphql", a)


def test_enrich_assigns_occurrence_for_duplicates():
    out = enrich("/", [el(name="Add new"), el(name="Add new"), el(name="Send")])
    assert [e["occ"] for e in out] == [0, 1, 0]
    assert out[0]["eid"] == out[1]["eid"]  # same element identity, different on-screen occurrence
    assert out[0]["sig"] == element_signature("/", out[0])


def test_actionable_rules():
    assert is_actionable(el(), DENY, {}) == ("click", None)
    assert is_actionable(el(name="Login"), DENY, {})[0] is None
    assert is_actionable(el(name="", tooltip="Delete request"), DENY, {})[0] is None
    assert is_actionable(el(external=True), DENY, {})[0] is None
    assert is_actionable(el(disabled=True), DENY, {})[0] is None
    assert is_actionable(el(kind="label", role="heading"), DENY, {})[0] is None
    assert is_actionable(el(role="textbox", name="Key", placeholder="Key"), DENY, {}) == (None, "no fixture")
    assert is_actionable(el(role="textbox", name="Key", placeholder="Key"), DENY, {"key": "x-trace"}) == ("fill", "x-trace")


def test_service_host_aliases_failover_hosts():
    from triangulate.crawl.browser import service_host
    aliases = {"algolia.net": "algolia", "algolianet.com": "algolia"}
    assert service_host("jrx67evef2-dsn.algolia.net", aliases) == "algolia"
    assert service_host("jrx67evef2-2.algolianet.com", aliases) == "algolia"
    assert service_host("echo.hoppscotch.io", aliases) == "echo.hoppscotch.io"
    assert service_host("notalgolia.net", aliases) == "notalgolia.net"   # suffix match only on a dot boundary

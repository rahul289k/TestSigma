"""Identity rules for the UI layer: element signatures, element ids and state fingerprints.

These rules decide what counts as "the same element" and "the same screen", so they are kept
small, pure and unit-tested. Data that changes between visits (numbers, editor contents) is
masked so that two visits to the same screen produce the same fingerprint.
"""

from __future__ import annotations

import hashlib
import re

CLICKABLE_ROLES = {"button", "link", "tab", "menuitem", "menuitemradio", "menuitemcheckbox", "checkbox",
                   "switch", "radio", "option", "combobox"}


def norm_name(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"[⌘⌥⇧↩⏎⌃]", " ", s)
    s = re.sub(r"\d+", "#", s)
    return re.sub(r"\s+", " ", s).strip()


def element_signature(route: str, el: dict) -> str:
    return f"{route}|{el.get('context', '')}|{el.get('role', '')}|{norm_name(el.get('name', ''))}"


def element_id(signature: str) -> str:
    return "E-" + hashlib.sha1(signature.encode()).hexdigest()[:12]


def fingerprint(route: str, elements: list[dict]) -> str:
    items = sorted({
        (e.get("role", ""), norm_name(e.get("name", "")), e.get("context", ""),
         bool(e.get("selected")), bool(e.get("expanded")), bool(e.get("checked")))
        for e in elements
    })
    blob = route + "\n" + "\n".join("|".join(map(str, it)) for it in items)
    return "S-" + hashlib.sha1(blob.encode()).hexdigest()[:12]


def enrich(route: str, elements: list[dict]) -> list[dict]:
    """Add signature, element id and per-state occurrence index (for replay) to raw observations."""
    seen: dict[str, int] = {}
    out = []
    for e in elements:
        sig = element_signature(route, e)
        k = seen.get(sig, 0)
        seen[sig] = k + 1
        out.append({**e, "sig": sig, "eid": element_id(sig), "occ": k})
    return out


def is_denied(el: dict, deny_names: list[str]) -> str | None:
    hay = " ".join(str(el.get(f, "")) for f in ("name", "tooltip", "text", "aria_label", "title")).lower()
    for d in deny_names:
        if d in hay:
            return d
    return None


def is_actionable(el: dict, deny_names: list[str], fixtures: dict[str, str]) -> tuple[str | None, str | None]:
    """Return (action kind, fill value) or (None, reason) for why the element is not acted on."""
    if el.get("kind") != "action":
        return None, "label"
    if el.get("disabled"):
        return None, "disabled"
    if el.get("external") or el.get("target_blank"):
        return None, "external link"
    denied = is_denied(el, deny_names)
    if denied:
        return None, f"denied:{denied}"
    role = el.get("role")
    if role in CLICKABLE_ROLES:
        return "click", None
    if role == "textbox":
        key = (el.get("placeholder") or el.get("name") or "").lower()
        for fk, fv in sorted(fixtures.items()):
            if fk.lower() in key:
                return "fill", fv
        return None, "no fixture"
    return None, f"role {role}"

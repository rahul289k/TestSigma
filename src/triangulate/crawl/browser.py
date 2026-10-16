"""A guarded Playwright session: fresh context per episode, blocked third parties, same-origin only.

Guardrails live here, in code, so neither the systematic explorer nor the LLM probe agent can
bypass them: navigation away from the app origin is aborted, popups are closed, browser dialogs
are dismissed and downloads are refused.
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from ..config import CrawlCfg
from .model import enrich, fingerprint

OBSERVE_JS = (Path(__file__).parent / "observe.js").read_text()
SETTLE_JS = "(() => document.querySelectorAll('*').length + ':' + (document.body ? document.body.innerText.length : 0))()"
APP_READY_JS = """() => [...document.querySelectorAll('button, a[href], input, textarea, [role=button], [role=tab]')]
    .filter(e => e.offsetParent !== null).length >= 3"""


def service_host(netloc: str, aliases: dict[str, str]) -> str:
    """The name recorded for a host: its service alias when the host is (a subdomain of) an aliased domain."""
    return next((alias for domain, alias in aliases.items() if netloc == domain or netloc.endswith("." + domain)), netloc)


class ActionFailed(RuntimeError):
    pass


class Session:
    def __init__(self, app_url: str, cfg: CrawlCfg):
        self.app_url = app_url.rstrip("/")
        self.origin = urlparse(app_url).netloc
        self.cfg = cfg
        self._pw = sync_playwright().start()
        self.browser: Browser = self._pw.chromium.launch()
        self.ctx: BrowserContext | None = None
        self.page: Page | None = None
        self.network: list[str] = []
        self.inflight: set = set()
        self.page_loaded = False  # a full document load started since the last settle
        self.blocked_navigations = 0
        self.popups_closed = 0
        self.resets = 0
        self.load_retries = 0

    # ---- lifecycle -----------------------------------------------------------------------
    def reset(self, route: str, attempts: int = 3) -> None:
        """Fresh context (clean storage) and load the route. A slow page load is retried (same request,
        new context); after the last attempt the error propagates."""
        for attempt in range(attempts):
            try:
                self._reset_once(route)
                return
            except Exception as e:  # Playwright TimeoutError / navigation error
                self.load_retries += 1
                if attempt == attempts - 1:
                    raise ActionFailed(f"loading {route} failed {attempts} times: {e.__class__.__name__}") from e
                time.sleep(5 * (attempt + 1))

    def _reset_once(self, route: str) -> None:
        if self.ctx:
            self.ctx.close()
        self.resets += 1
        self.ctx = self.browser.new_context(
            viewport=self.cfg.viewport, locale=self.cfg.locale, timezone_id=self.cfg.timezone,
            accept_downloads=False, service_workers="block", reduced_motion="reduce",
        )
        self.ctx.route("**/*", self._route_handler)
        self.page = None
        self.page = self.ctx.new_page()
        # Registered after our own page exists: any later page in this context is a popup.
        self.ctx.on("page", self._on_popup)
        self.page.on("dialog", lambda d: d.dismiss())
        self.page.on("request", self._on_request)
        self.page.on("requestfinished", self._on_request_done)
        self.page.on("requestfailed", self._on_request_done)
        self.inflight = set()
        self.page.set_default_timeout(self.cfg.nav_timeout_ms)
        self.page.goto(self.app_url + route, wait_until="domcontentloaded")
        self._wait_app()
        self.page_loaded = False
        self.settle()

    def _wait_app(self) -> None:
        """After a full document load: wait until the single-page app has mounted."""
        assert self.page is not None
        self.page.wait_for_load_state("domcontentloaded")
        # "#app has children" is not enough: the loading splash is a child too. Mounted = real controls visible.
        self.page.wait_for_function(APP_READY_JS, timeout=self.cfg.nav_timeout_ms)
        self.page.wait_for_timeout(1500)

    def close(self) -> None:
        try:
            if self.ctx:
                self.ctx.close()
            self.browser.close()
        finally:
            self._pw.stop()

    # ---- guards ----------------------------------------------------------------------------
    def _route_handler(self, route) -> None:
        req = route.request
        host = urlparse(req.url).netloc
        if any(b in host for b in self.cfg.block_hosts):
            return route.abort()
        if req.is_navigation_request() and req.frame == (self.page.main_frame if self.page else None) and host and host != self.origin:
            self.blocked_navigations += 1
            return route.abort()
        return route.continue_()

    def _on_popup(self, popup: Page) -> None:
        if self.page is not None and popup != self.page:
            self.popups_closed += 1
            popup.close()

    def _on_request(self, req) -> None:
        u = urlparse(req.url)
        ignored = any(h in u.netloc for h in [*self.cfg.block_hosts, *self.cfg.network_ignore_hosts])
        if req.resource_type in ("xhr", "fetch", "websocket", "eventsource") and not ignored:
            self.network.append(f"{req.method} {service_host(u.netloc, self.cfg.network_host_aliases)}{u.path}")
        # Data AND code: a route's code is downloaded on first visit, and until it arrives the old screen stays
        # on display, so observing early records the previous page.
        if req.resource_type in ("xhr", "fetch", "script", "stylesheet") and not any(h in u.netloc for h in self.cfg.block_hosts):
            self.inflight.add(req)
        if req.is_navigation_request() and self.page is not None and req.frame == self.page.main_frame:
            # A control that reloads the app: observing before the new document has booted would record
            # a half-loaded or stale screen, so settle() waits for it.
            self.inflight.add(req)
            self.page_loaded = True

    def _on_request_done(self, req) -> None:
        self.inflight.discard(req)

    # ---- observe / act ---------------------------------------------------------------------
    def settle(self) -> None:
        """Wait until no fetch/XHR, code download or page load is in flight, no transient label is shown, AND the DOM has
        stopped changing (bounded). A full page load restarts the wait once the app has mounted again."""
        assert self.page is not None
        self.page.wait_for_timeout(self.cfg.settle_ms)
        deadline = time.time() + self.cfg.settle_timeout_ms / 1000
        last, stable = None, 0
        while time.time() < deadline:
            if self.page_loaded and not self.inflight:
                self.page_loaded = False
                try:
                    self._wait_app()
                except PlaywrightError as e:
                    raise ActionFailed(f"page reload did not finish: {e.__class__.__name__}") from e
                deadline = time.time() + self.cfg.settle_timeout_ms / 1000
                last, stable = None, 0
                continue
            try:
                sig = self.page.evaluate(SETTLE_JS)
            except PlaywrightError:  # the document was replaced mid-check
                last, stable = None, 0
                self.page.wait_for_timeout(150)
                continue
            stable = stable + 1 if sig == last else 0
            if stable >= 2 and not self.inflight and not self.page_loaded and not self._transient_visible():
                return
            last = sig
            self.page.wait_for_timeout(150)

    def _transient_visible(self) -> bool:
        if not self.cfg.transient_labels:
            return False
        assert self.page is not None
        try:
            visible = self.page.evaluate(
                "(labels) => [...document.querySelectorAll('button, [role=button], [role=status], span')]"
                ".some(e => e.offsetParent !== null && labels.includes((e.innerText || '').trim()))",
                self.cfg.transient_labels)
        except PlaywrightError:  # the document was replaced mid-check: not settled
            return True
        return bool(visible)

    def observe(self) -> dict:
        assert self.page is not None
        raw = self.page.evaluate(OBSERVE_JS)
        route = raw["path"].rstrip("/") or "/"
        elements = enrich(route, raw["elements"])
        return {"route": route, "url": raw["url"], "title": raw["title"], "elements": elements,
                "fp": fingerprint(route, elements)}

    def find(self, obs: dict, sig: str, occ: int) -> dict | None:
        matches = [e for e in obs["elements"] if e["sig"] == sig]
        return matches[occ] if occ < len(matches) else None

    def act(self, el: dict, kind: str, value: str | None = None) -> list[str]:
        assert self.page is not None
        self.network = []
        loc = self.page.locator(f'[data-tri-id="{el["tri"]}"]')
        try:
            if kind == "click":
                loc.click(timeout=4000)
            elif kind == "fill":
                if el.get("editor"):
                    loc.click(timeout=4000)
                    self.page.keyboard.press("Meta+A")
                    self.page.keyboard.type(value or "")
                else:
                    loc.fill(value or "", timeout=4000)
            elif kind == "press":
                self.page.keyboard.press(value or "Escape")
            else:
                raise ActionFailed(f"unknown action kind {kind}")
        except ActionFailed:
            raise
        except Exception as e:  # Playwright timeout / detached element
            raise ActionFailed(f"{kind} on '{el.get('name')}' failed: {e.__class__.__name__}") from e
        self.settle()
        return sorted(set(self.network))

    def lang(self) -> str:
        assert self.page is not None
        return self.page.evaluate("document.documentElement.lang || ''")

    def press(self, key: str) -> None:
        assert self.page is not None
        self.page.keyboard.press(key)
        self.settle()

    def screenshot(self, path: Path) -> None:
        assert self.page is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), type="jpeg", quality=70)

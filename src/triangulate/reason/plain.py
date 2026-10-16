"""Plain-language names for code areas and routes, for reader-facing report text."""

from __future__ import annotations

import re

ROUTE_NAMES = {"/": "the REST page", "/graphql": "the GraphQL page", "/settings": "Settings",
               "/realtime/websocket": "the Realtime WebSocket page", "/realtime/sse": "the Realtime SSE page",
               "/realtime/socketio": "the Realtime Socket.IO page", "/realtime/mqtt": "the Realtime MQTT page"}


def route_name(route: str) -> str:
    return ROUTE_NAMES.get(route, f"the {route.strip('/').replace('/', ' ')} page")


def area_name(path: str) -> str:
    """'src/components/collections/documentation/sections/Response.vue' -> 'Collections › Documentation › Sections › Response'"""
    parts = path.removeprefix("src/").removesuffix(".vue").removesuffix(".ts").split("/")
    parts = [p for p in parts if p not in ("components", "index", "pages", "helpers")]
    words = [re.sub(r"([a-z])([A-Z])", r"\1 \2", p).replace("-", " ").replace("_", " ") for p in parts]
    return " › ".join(w[:1].upper() + w[1:] for w in words if w) or path

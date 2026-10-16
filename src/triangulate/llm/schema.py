"""Convert a Pydantic model into Gemini's responseSchema (OpenAPI subset).

Only the shapes our models use are supported: objects, arrays, strings (incl. Literal enums),
numbers, integers, booleans and Optional[X]. Anything else raises, so a schema we can't
express faithfully never reaches the API.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_TYPES = {"string": "STRING", "integer": "INTEGER", "number": "NUMBER", "boolean": "BOOLEAN"}


def to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    js = model.model_json_schema()
    defs = js.get("$defs", {})

    def conv(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            return conv(defs[node["$ref"].split("/")[-1]])
        if "anyOf" in node:
            non_null = [n for n in node["anyOf"] if n.get("type") != "null"]
            if len(non_null) != 1:
                raise ValueError(f"Unsupported union in schema {model.__name__}: {node}")
            out = conv(non_null[0])
            out["nullable"] = True
            return out
        if "enum" in node or "const" in node:
            values = node["enum"] if "enum" in node else [node["const"]]
            out: dict[str, Any] = {"type": "STRING", "enum": [str(e) for e in values]}
        elif node.get("type") == "object":
            props = node.get("properties", {})
            out = {"type": "OBJECT", "properties": {k: conv(v) for k, v in props.items()},
                   "required": list(node.get("required", [])), "propertyOrdering": list(props)}
        elif node.get("type") == "array":
            out = {"type": "ARRAY", "items": conv(node["items"])}
            if "minItems" in node:
                out["minItems"] = node["minItems"]
        elif node.get("type") in _TYPES:
            out = {"type": _TYPES[node["type"]]}
        else:
            raise ValueError(f"Unsupported schema node in {model.__name__}: {node}")
        if node.get("description"):
            out["description"] = node["description"]
        return out

    return conv(js)

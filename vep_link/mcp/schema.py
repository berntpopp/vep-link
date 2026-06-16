"""Output-schema relaxation for MCP tool result validation.

Ensembl REST payloads drift over time (new VEP fields, extra plugin keys).
``relax_output_schema`` deep-copies a JSON Schema and stamps
``additionalProperties=True`` on every object node so that upstream field drift
never fails MCP output validation. The function is pure: the input schema is
never mutated.
"""

from __future__ import annotations

import copy
from typing import Any

# JSON Schema keywords whose values are themselves schemas (single schema).
_SCHEMA_VALUE_KEYS = ("items", "additionalItems", "contains", "not", "if", "then", "else")
# Keywords whose values map names to schemas.
_SCHEMA_MAP_KEYS = ("properties", "patternProperties", "$defs", "definitions")
# Keywords whose values are lists of schemas.
_SCHEMA_LIST_KEYS = ("allOf", "anyOf", "oneOf", "prefixItems")


def relax_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``schema`` with ``additionalProperties=True`` set
    on every object node (root, nested objects, and objects inside arrays).

    Pure function: the input ``schema`` is not mutated.
    """
    relaxed = copy.deepcopy(schema)
    _relax_in_place(relaxed)
    return relaxed


def _relax_in_place(node: Any) -> None:
    if isinstance(node, dict):
        if _is_object_node(node):
            node["additionalProperties"] = True
        for key in _SCHEMA_MAP_KEYS:
            child = node.get(key)
            if isinstance(child, dict):
                for sub in child.values():
                    _relax_in_place(sub)
        for key in _SCHEMA_VALUE_KEYS:
            if key in node:
                _relax_in_place(node[key])
        for key in _SCHEMA_LIST_KEYS:
            child = node.get(key)
            if isinstance(child, list):
                for sub in child:
                    _relax_in_place(sub)
    elif isinstance(node, list):
        for sub in node:
            _relax_in_place(sub)


def _is_object_node(node: dict[str, Any]) -> bool:
    """A node is an object schema if it declares ``type: object`` or carries
    object-only keywords (``properties`` / ``patternProperties``)."""
    node_type = node.get("type")
    if node_type == "object":
        return True
    if isinstance(node_type, list) and "object" in node_type:
        return True
    return "properties" in node or "patternProperties" in node

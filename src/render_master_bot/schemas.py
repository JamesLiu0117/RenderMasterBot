"""JSON Schema projections for model generation and strict validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

from render_master_bot.contracts import CONTRACT_MODELS
from render_master_bot.models import RenderSpec


def contract_model(name: str) -> type[BaseModel]:
    """Resolve a public contract name to its strict Pydantic model."""

    try:
        return CONTRACT_MODELS[name]
    except KeyError as exc:
        choices = ", ".join(sorted(CONTRACT_MODELS))
        raise ValueError(f"unknown contract {name!r}; choose one of: {choices}") from exc


def contract_schema(name: str) -> dict[str, Any]:
    """Return the complete validation schema for a public contract."""

    return contract_model(name).model_json_schema()


def ollama_generation_schema() -> dict[str, Any]:
    """Return an Ollama-compatible projection of the strict RenderSpec schema.

    Ollama 0.32's grammar parser rejects the ``pattern`` keyword and the
    ``prefixItems`` representation produced for fixed homogeneous tuples.
    Pydantic still applies the complete schema after generation, so removing
    these two grammar hints does not weaken the application trust boundary.
    """

    schema = deepcopy(RenderSpec.model_json_schema())
    stack: list[Any] = [schema]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            value.pop("pattern", None)
            prefix_items = value.pop("prefixItems", None)
            if prefix_items:
                first = prefix_items[0]
                if not all(item == first for item in prefix_items):
                    raise ValueError("cannot simplify a heterogeneous tuple schema")
                value["items"] = first
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return schema

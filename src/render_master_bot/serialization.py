"""Stable serialization helpers for cross-component contract identity."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json_bytes(value: BaseModel | Any) -> bytes:
    """Serialize a contract deterministically before hashing or persistence."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text.encode("utf-8")


def canonical_sha256(value: BaseModel | Any) -> str:
    """Return the lowercase SHA-256 identity of canonical contract JSON."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

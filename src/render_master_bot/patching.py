"""Safe application of the public RenderSpecPatch contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from render_master_bot.contracts import PatchOperation, RenderSpecPatch
from render_master_bot.models import RenderSpec
from render_master_bot.serialization import canonical_sha256


class PatchApplicationError(RuntimeError):
    """Raised when a validated patch cannot be applied to its exact base spec."""


def _pointer_tokens(path: str) -> list[str]:
    tokens: list[str] = []
    for raw in path.split("/")[1:]:
        index = 0
        while index < len(raw):
            if raw[index] == "~" and (index + 1 == len(raw) or raw[index + 1] not in "01"):
                raise PatchApplicationError(f"invalid JSON Pointer escape in path: {path}")
            index += 1
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    if not tokens:
        raise PatchApplicationError("patch operations cannot replace the document root")
    return tokens


def _list_index(token: str, *, length: int, allow_end: bool, path: str) -> int:
    if not token.isdigit():
        raise PatchApplicationError(f"list path token must be an index at {path}: {token!r}")
    index = int(token)
    maximum = length if allow_end else length - 1
    if index > maximum:
        raise PatchApplicationError(f"list index is out of range at {path}: {index}")
    return index


def _parent(document: Any, tokens: list[str], path: str) -> tuple[Any, str]:
    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise PatchApplicationError(f"patch path does not exist: {path}")
            current = current[token]
        elif isinstance(current, list):
            current = current[
                _list_index(token, length=len(current), allow_end=False, path=path)
            ]
        else:
            raise PatchApplicationError(f"patch path traverses a scalar value: {path}")
    return current, tokens[-1]


def _apply_operation(document: dict[str, Any], operation: PatchOperation) -> None:
    parent, token = _parent(document, _pointer_tokens(operation.path), operation.path)
    value = deepcopy(operation.value)
    if isinstance(parent, dict):
        exists = token in parent
        if operation.op in {"replace", "remove"} and not exists:
            raise PatchApplicationError(f"patch path does not exist: {operation.path}")
        if operation.op == "remove":
            del parent[token]
        else:
            if operation.op == "replace" and parent[token] == value:
                raise PatchApplicationError(
                    f"replace operation does not change its target: {operation.path}"
                )
            parent[token] = value
        return

    if not isinstance(parent, list):
        raise PatchApplicationError(f"patch parent is not a container: {operation.path}")
    if token == "-":
        if operation.op != "add":
            raise PatchApplicationError(
                f"the '-' list token is only valid for add: {operation.path}"
            )
        parent.append(value)
        return
    index = _list_index(
        token,
        length=len(parent),
        allow_end=operation.op == "add",
        path=operation.path,
    )
    if operation.op == "add":
        parent.insert(index, value)
    elif operation.op == "replace":
        if parent[index] == value:
            raise PatchApplicationError(
                f"replace operation does not change its target: {operation.path}"
            )
        parent[index] = value
    else:
        del parent[index]


def apply_render_spec_patch(spec: RenderSpec, patch: RenderSpecPatch) -> RenderSpec:
    """Apply a bounded patch and revalidate the complete RenderSpec."""

    observed_hash = canonical_sha256(spec)
    if patch.base_spec_sha256 != observed_hash:
        raise PatchApplicationError(
            "patch base_spec_sha256 does not match the supplied RenderSpec: "
            f"expected {observed_hash}, received {patch.base_spec_sha256}"
        )
    document = spec.model_dump(mode="json")
    # The default auto policy is intentionally omitted from v0.1 canonical
    # serialization so historical hashes remain stable. It is still a real
    # model field and therefore must exist in the internal patch document.
    document["camera"].setdefault(
        "exposure",
        spec.camera.exposure.model_dump(mode="json"),
    )
    try:
        for operation in patch.operations:
            _apply_operation(document, operation)
        return RenderSpec.model_validate(document)
    except ValidationError as exc:
        raise PatchApplicationError(f"patched RenderSpec is invalid: {exc}") from exc

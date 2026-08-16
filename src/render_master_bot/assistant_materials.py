"""Approval-gated material proposals for one selected Unreal mesh component."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from render_master_bot.asset_index import (
    AssetIndexError,
    AssetSearchHit,
    load_asset_card_catalog,
)
from render_master_bot.contracts import (
    AssistantMaterialProposal,
    MaterialCandidate,
    UnrealSelectionContext,
)


class MaterialProposalError(RuntimeError):
    """Raised when selection evidence cannot produce a trustworthy proposal."""


class MaterialSearcher(Protocol):
    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        asset_types: list[str] | None = None,
    ) -> list[AssetSearchHit]: ...


def load_selection_context(path: str | Path) -> UnrealSelectionContext:
    context_path = Path(path).expanduser().resolve()
    try:
        value = json.loads(context_path.read_text(encoding="utf-8-sig"))
        return UnrealSelectionContext.model_validate(value)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise MaterialProposalError(
            f"cannot read Unreal selection context {context_path}: {exc}"
        ) from exc


def _object_path_base(path: str | None) -> str:
    return (path or "").casefold().split(".", maxsplit=1)[0]


def _candidate(hit: AssetSearchHit, *, engine_path: str, display_name: str) -> MaterialCandidate:
    return MaterialCandidate(
        rank=hit.rank,
        asset_id=hit.asset_id,
        display_name=display_name,
        engine_path=engine_path,
        similarity=hit.similarity,
    )


def propose_material_change(
    *,
    prompt: str,
    context: UnrealSelectionContext,
    asset_catalog_path: str | Path,
    searcher: MaterialSearcher,
    embedding_model: str,
    limit: int = 5,
    proposal_id: str = "material_proposal",
) -> AssistantMaterialProposal:
    """Retrieve and validate one reversible material action without editing Unreal."""

    request = prompt.strip()
    if not request:
        raise MaterialProposalError("material request cannot be empty")
    if not 1 <= limit <= 5:
        raise MaterialProposalError("material proposal limit must be between 1 and 5")

    try:
        catalog = load_asset_card_catalog(asset_catalog_path)
    except AssetIndexError as exc:
        raise MaterialProposalError(str(exc)) from exc
    materials = {card.asset_id: card for card in catalog if card.asset_type == "material"}

    if context.target_slot_index is None and len(context.material_slots) != 1:
        return AssistantMaterialProposal(
            proposal_id=proposal_id,
            status="unresolved",
            request=request,
            target=context,
            proposed_by={"provider": "local", "model": embedding_model},
            rationale=(
                "The first material-editing capability requires exactly one observed material "
                "slot so it cannot silently change the wrong surface."
            ),
            missing_capabilities=["explicit multi-slot material targeting"],
        )

    target_slot = (
        context.material_slots[0]
        if context.target_slot_index is None
        else next(
            slot
            for slot in context.material_slots
            if slot.slot_index == context.target_slot_index
        )
    )

    try:
        hits = searcher.search(request, limit=limit, asset_types=["material"])
    except AssetIndexError as exc:
        raise MaterialProposalError(str(exc)) from exc

    current_path = _object_path_base(target_slot.current_material_path)
    candidates: list[MaterialCandidate] = []
    for hit in hits:
        card = materials.get(hit.asset_id)
        if card is None:
            raise MaterialProposalError(
                f"retrieval returned material outside the supplied catalog: {hit.asset_id}"
            )
        if _object_path_base(card.engine_path) == current_path:
            continue
        candidates.append(
            _candidate(hit, engine_path=card.engine_path, display_name=card.display_name)
        )

    if not candidates:
        return AssistantMaterialProposal(
            proposal_id=proposal_id,
            status="unresolved",
            request=request,
            target=context,
            proposed_by={"provider": "local", "model": embedding_model},
            rationale="No different project material matched the request.",
            missing_capabilities=["matching project material asset"],
        )

    selected = candidates[0]
    return AssistantMaterialProposal(
        proposal_id=proposal_id,
        status="proposed",
        request=request,
        target=context,
        proposed_by={"provider": "local", "model": embedding_model},
        selected_slot=target_slot,
        selected_material=selected,
        alternatives=candidates[1:],
        rationale=(
            "Selected the highest-ranked catalog-verified material that differs from the "
            "currently assigned material. User approval and Unreal target revalidation are "
            "required before application."
        ),
    )

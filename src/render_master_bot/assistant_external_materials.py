"""Editor-facing orchestration for reviewing an external material before import."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from render_master_bot.external_materials import (
    ExternalMaterialAcquisition,
    ExternalMaterialCandidate,
    ExternalMaterialSearchReport,
    TextEmbedder,
    acquire_polyhaven_material,
    discover_polyhaven_materials,
)
from render_master_bot.material_import_workflow import (
    ExternalMaterialImportProposal,
    create_external_material_import_proposal,
)
from render_master_bot.models import StrictModel
from render_master_bot.serialization import canonical_sha256


class ExternalMaterialAssistantError(RuntimeError):
    """Raised when the Editor-facing external material proposal cannot be prepared."""


class ExternalMaterialAssistantProposal(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    proposal_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
    status: Literal["pending_approval"] = "pending_approval"
    query: str = Field(min_length=1, max_length=4000)
    provider: Literal["polyhaven"] = "polyhaven"
    provider_credit: Literal["Powered by Poly Haven"] = "Powered by Poly Haven"
    provider_asset_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    source_url: str = Field(min_length=1, max_length=1000)
    license: Literal["CC0-1.0"] = "CC0-1.0"
    license_url: str = Field(min_length=1, max_length=1000)
    resolution: str = Field(pattern=r"^(1k|2k|4k|8k)$")
    image_format: str = Field(pattern=r"^(jpg|png)$")
    downloaded_map_count: Literal[4] = 4
    import_proposal_path: str = Field(min_length=1, max_length=1000)
    import_proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    destination_path: str = Field(min_length=1, max_length=500)
    material_name: str = Field(min_length=1, max_length=100)
    planned_asset_paths: list[str] = Field(min_length=5, max_length=5)


def _write_contract(path: Path, value: StrictModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _unreal_asset_suffix(provider_asset_id: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", provider_asset_id) if part]
    suffix = "".join(part[:1].upper() + part[1:] for part in parts)
    if not suffix or not suffix[0].isalpha():
        raise ExternalMaterialAssistantError("provider asset ID cannot form an Unreal asset name")
    return suffix[:72]


def _assistant_import_names(candidate: ExternalMaterialCandidate) -> tuple[str, str]:
    suffix = _unreal_asset_suffix(candidate.provider_asset_id)
    return (
        f"/Game/RenderMasterBot/Imported/PolyHaven/{suffix}",
        f"M_PH_{suffix}",
    )


def prepare_external_material_assistant_proposal(
    *,
    query: str,
    embedder: TextEmbedder,
    embedding_model: str,
    library_root: str | Path,
    work_directory: str | Path,
    proposal_id: str,
    resolution: str = "1k",
    image_format: str = "jpg",
    timeout_seconds: float = 60.0,
) -> tuple[
    ExternalMaterialSearchReport,
    ExternalMaterialAcquisition,
    ExternalMaterialImportProposal,
    ExternalMaterialAssistantProposal,
]:
    """Search, cache, verify, and freeze the top candidate without editing Unreal."""

    work = Path(work_directory).expanduser().resolve()
    search_path = work / "external_search.json"
    acquisition_path = work / "external_acquisition.json"
    import_proposal_path = work / "external_import_proposal.json"
    search = discover_polyhaven_materials(
        query=query,
        embedder=embedder,
        embedding_model=embedding_model,
        limit=5,
        timeout_seconds=timeout_seconds,
    )
    if not search.candidates:
        raise ExternalMaterialAssistantError("Poly Haven returned no material candidates")
    candidate = search.candidates[0]
    acquisition = acquire_polyhaven_material(
        candidate,
        destination_root=library_root,
        resolution=resolution,
        image_format=image_format,
        timeout_seconds=timeout_seconds,
    )
    _write_contract(search_path, search)
    _write_contract(acquisition_path, acquisition)
    destination_path, material_name = _assistant_import_names(candidate)
    proposal = create_external_material_import_proposal(
        acquisition_path=acquisition_path,
        destination_path=destination_path,
        material_name=material_name,
        proposal_id=proposal_id,
    )
    _write_contract(import_proposal_path, proposal)
    assistant_proposal = ExternalMaterialAssistantProposal(
        proposal_id=proposal_id,
        query=query.strip(),
        provider_asset_id=candidate.provider_asset_id,
        display_name=candidate.display_name,
        description=candidate.description,
        source_url=candidate.asset_url,
        license_url=candidate.license_url,
        resolution=acquisition.resolution,
        image_format=acquisition.image_format,
        import_proposal_path=str(import_proposal_path),
        import_proposal_sha256=canonical_sha256(proposal),
        destination_path=proposal.destination_path,
        material_name=proposal.material_name,
        planned_asset_paths=proposal.planned_asset_paths,
    )
    return search, acquisition, proposal, assistant_proposal

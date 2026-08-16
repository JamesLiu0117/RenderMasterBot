"""Approval-gated external material import bound to frozen acquisition evidence."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from render_master_bot.external_materials import ExternalMaterialAcquisition
from render_master_bot.models import StrictModel
from render_master_bot.serialization import canonical_sha256
from render_master_bot.unreal_materials import (
    PbrMaterialImportResult,
    UnrealMaterialImportError,
    prepare_pbr_material_import_request,
    run_unreal_pbr_material_import,
)


class MaterialImportWorkflowError(RuntimeError):
    """Raised when approval or frozen acquisition evidence is invalid."""


class ExternalMaterialImportProposal(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    proposal_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
    status: Literal["pending_approval"] = "pending_approval"
    acquisition_path: str = Field(min_length=1, max_length=1000)
    acquisition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: Literal["polyhaven"] = "polyhaven"
    provider_asset_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=240)
    source_url: str = Field(min_length=1, max_length=1000)
    license: Literal["CC0-1.0"] = "CC0-1.0"
    license_url: str = Field(min_length=1, max_length=1000)
    provider_credit: Literal["Powered by Poly Haven"] = "Powered by Poly Haven"
    destination_path: str = Field(min_length=1, max_length=500)
    material_name: str = Field(min_length=1, max_length=100)
    planned_asset_paths: list[str] = Field(min_length=5, max_length=5)
    modifies_project_content: Literal[True] = True
    saves_assets: Literal[True] = True
    approval_required: Literal[True] = True
    proposed_at: datetime


class MaterialImportApproval(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_by: str = Field(min_length=1, max_length=120)
    acknowledgement: Literal["create-and-save-five-unreal-assets"]
    approved_at: datetime


class MaterialImportExecution(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    status: Literal["succeeded"] = "succeeded"
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval: MaterialImportApproval
    import_result: PbrMaterialImportResult

    @model_validator(mode="after")
    def approval_matches_execution(self) -> "MaterialImportExecution":
        if self.approval.proposal_sha256 != self.proposal_sha256:
            raise ValueError("material import approval does not match the execution")
        return self


def load_external_material_acquisition(
    path: str | Path,
) -> ExternalMaterialAcquisition:
    try:
        return ExternalMaterialAcquisition.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise MaterialImportWorkflowError(f"invalid material acquisition: {exc}") from exc


def load_external_material_import_proposal(
    path: str | Path,
) -> ExternalMaterialImportProposal:
    try:
        return ExternalMaterialImportProposal.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise MaterialImportWorkflowError(f"invalid material import proposal: {exc}") from exc


def load_material_import_execution(path: str | Path) -> MaterialImportExecution:
    try:
        return MaterialImportExecution.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise MaterialImportWorkflowError(f"invalid material import execution: {exc}") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_map_paths(
    acquisition: ExternalMaterialAcquisition,
) -> dict[str, Path]:
    maps: dict[str, Path] = {}
    for value in acquisition.maps:
        path = Path(value.local_path).expanduser().resolve()
        if not path.is_file():
            raise MaterialImportWorkflowError(f"acquired map is missing: {path}")
        if path.stat().st_size != value.downloaded_size:
            raise MaterialImportWorkflowError(f"acquired map size changed: {path}")
        if _file_sha256(path) != value.source_sha256:
            raise MaterialImportWorkflowError(f"acquired map SHA-256 changed: {path}")
        maps[value.role] = path
    if set(maps) != {"base_color", "normal", "roughness", "ambient_occlusion"}:
        raise MaterialImportWorkflowError("acquisition does not contain four unique PBR roles")
    return maps


def create_external_material_import_proposal(
    *,
    acquisition_path: str | Path,
    destination_path: str,
    material_name: str,
    proposal_id: str,
) -> ExternalMaterialImportProposal:
    resolved_acquisition_path = Path(acquisition_path).expanduser().resolve()
    acquisition = load_external_material_acquisition(resolved_acquisition_path)
    maps = _verified_map_paths(acquisition)
    request = prepare_pbr_material_import_request(
        destination_path=destination_path,
        material_name=material_name,
        **maps,
    )
    planned_assets = [
        f"{request.destination_path}/{texture.destination_name}"
        for texture in request.textures
    ] + [f"{request.destination_path}/{request.material_name}"]
    if len(planned_assets) != len(set(planned_assets)):
        raise MaterialImportWorkflowError("planned Unreal material assets are not unique")
    candidate = acquisition.candidate
    return ExternalMaterialImportProposal(
        proposal_id=proposal_id,
        acquisition_path=str(resolved_acquisition_path),
        acquisition_sha256=canonical_sha256(acquisition),
        provider_asset_id=candidate.provider_asset_id,
        display_name=candidate.display_name,
        source_url=candidate.asset_url,
        license_url=candidate.license_url,
        destination_path=request.destination_path,
        material_name=request.material_name,
        planned_asset_paths=planned_assets,
        proposed_at=datetime.now(UTC),
    )


def execute_external_material_import(
    proposal_path: str | Path,
    *,
    approved_proposal_sha256: str,
    approved_by: str,
    uproject_path: str | Path,
    engine_root: str | Path,
    import_output: str | Path,
    timeout_seconds: int = 300,
) -> MaterialImportExecution:
    proposal = load_external_material_import_proposal(proposal_path)
    observed_proposal_sha256 = canonical_sha256(proposal)
    if approved_proposal_sha256 != observed_proposal_sha256:
        raise MaterialImportWorkflowError(
            "approval SHA-256 does not match the exact material import proposal"
        )
    acquisition = load_external_material_acquisition(proposal.acquisition_path)
    if canonical_sha256(acquisition) != proposal.acquisition_sha256:
        raise MaterialImportWorkflowError("material acquisition changed after proposal")
    if acquisition.candidate.provider_asset_id != proposal.provider_asset_id:
        raise MaterialImportWorkflowError("material provider asset changed after proposal")
    maps = _verified_map_paths(acquisition)
    try:
        _, result = run_unreal_pbr_material_import(
            uproject_path,
            engine_root=engine_root,
            destination_path=proposal.destination_path,
            material_name=proposal.material_name,
            output=import_output,
            timeout_seconds=timeout_seconds,
            **maps,
        )
    except UnrealMaterialImportError as exc:
        raise MaterialImportWorkflowError(str(exc)) from exc
    approval = MaterialImportApproval(
        proposal_sha256=observed_proposal_sha256,
        approved_by=approved_by,
        acknowledgement="create-and-save-five-unreal-assets",
        approved_at=datetime.now(UTC),
    )
    return MaterialImportExecution(
        proposal_sha256=observed_proposal_sha256,
        approval=approval,
        import_result=result,
    )

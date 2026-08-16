"""Merge one approved external material into the catalog without losing prior assets."""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from render_master_bot.asset_index import AssetIndexReport, load_asset_card_catalog
from render_master_bot.contracts import AssetCard, SourceReference
from render_master_bot.external_materials import ExternalMaterialAcquisition
from render_master_bot.models import StrictModel
from render_master_bot.serialization import canonical_sha256
from render_master_bot.unreal_materials import PbrMaterialImportResult


class MaterialCatalogSyncError(RuntimeError):
    """Raised when imported assets cannot be merged into the searchable catalog."""


class MaterialCatalogSyncEvidence(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    status: Literal["succeeded"] = "succeeded"
    catalog_path: str = Field(min_length=1, max_length=1000)
    backup_path: str = Field(min_length=1, max_length=1000)
    raw_scan_path: str = Field(min_length=1, max_length=1000)
    before_count: int = Field(ge=1)
    after_count: int = Field(ge=1)
    imported_asset_ids: list[str] = Field(min_length=5, max_length=5)
    chroma_collection: str = Field(min_length=1, max_length=240)
    embedding_model: str = Field(min_length=1, max_length=240)
    index_inserted: int = Field(ge=0)
    index_updated: int = Field(ge=0)
    index_deleted: int = Field(ge=0)
    synced_at: datetime


def _identifier(value: str, *, fallback: str = "tag") -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9]+", "_", ascii_value.casefold()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"{fallback}_{normalized}".rstrip("_")
    return normalized[:64]


def _unique_tags(*groups: list[str]) -> list[str]:
    tags: list[str] = []
    for value in (item for group in groups for item in group):
        tag = _identifier(value)
        if tag not in tags:
            tags.append(tag)
    return tags[:64]


def enrich_imported_asset_cards(
    scanned_cards: list[AssetCard],
    *,
    acquisition: ExternalMaterialAcquisition,
    import_result: PbrMaterialImportResult,
) -> list[AssetCard]:
    """Attach exact CC0 provenance to the five assets from an approved import."""

    expected_paths = {
        import_result.material_engine_path,
        *(texture.engine_path for texture in import_result.textures),
    }
    observed_paths = {card.engine_path for card in scanned_cards}
    if observed_paths != expected_paths or len(scanned_cards) != 5:
        missing = sorted(expected_paths - observed_paths)
        unexpected = sorted(observed_paths - expected_paths)
        raise MaterialCatalogSyncError(
            "imported asset scan must contain the exact five executed assets; "
            f"missing={missing} unexpected={unexpected}"
        )

    candidate = acquisition.candidate
    acquisition_sha256 = canonical_sha256(acquisition)
    texture_by_path = {texture.engine_path: texture for texture in import_result.textures}
    acquired_by_role = {value.role: value for value in acquisition.maps}
    if set(acquired_by_role) != {
        "base_color",
        "normal",
        "roughness",
        "ambient_occlusion",
    }:
        raise MaterialCatalogSyncError("acquisition does not contain four unique PBR roles")

    enriched: list[AssetCard] = []
    for card in scanned_cards:
        generated_sources = list(card.sources)
        common_tags = [
            "polyhaven",
            "cc0",
            "pbr",
            candidate.provider_asset_id,
            candidate.category,
            *candidate.tags,
        ]
        if card.engine_path == import_result.material_engine_path:
            source = SourceReference(
                source_id=_identifier(f"polyhaven_{candidate.provider_asset_id}"),
                source_type="dataset",
                title=f"Poly Haven: {candidate.display_name}",
                uri=candidate.asset_url,
                license=candidate.license,
                content_sha256=acquisition_sha256,
            )
            description = (
                f"CC0 PBR material imported from Poly Haven asset "
                f"{candidate.display_name}. {candidate.description}"
            )[:4000]
            tags = _unique_tags(card.tags, common_tags, ["material"])
        else:
            imported_texture = texture_by_path[card.engine_path]
            acquired_map = acquired_by_role[imported_texture.role]
            if acquired_map.source_sha256 != imported_texture.source_sha256:
                raise MaterialCatalogSyncError(
                    f"import evidence hash differs from acquisition for {imported_texture.role}"
                )
            source = SourceReference(
                source_id=_identifier(
                    f"polyhaven_{candidate.provider_asset_id}_{imported_texture.role}"
                ),
                source_type="dataset",
                title=(
                    f"Poly Haven: {candidate.display_name} "
                    f"{imported_texture.role.replace('_', ' ')} map"
                ),
                uri=acquired_map.source_url,
                license=candidate.license,
                content_sha256=acquired_map.source_sha256,
            )
            description = (
                f"CC0 {imported_texture.role.replace('_', ' ')} texture for Poly Haven "
                f"material {candidate.display_name}; imported as {acquisition.resolution} "
                f"{acquisition.image_format.upper()}."
            )
            tags = _unique_tags(card.tags, common_tags, [imported_texture.role, "texture"])
        enriched.append(
            card.model_copy(
                update={
                    "description": description,
                    "tags": tags,
                    "license": candidate.license,
                    "sources": [*generated_sources, source],
                }
            )
        )
    return sorted(enriched, key=lambda card: card.engine_path.casefold())


def merge_asset_cards(
    existing_cards: list[AssetCard],
    imported_cards: list[AssetCard],
) -> list[AssetCard]:
    """Replace matching engine paths and preserve every unrelated existing card."""

    if not existing_cards:
        raise MaterialCatalogSyncError("existing asset catalog cannot be empty")
    imported_paths = [card.engine_path for card in imported_cards]
    if len(imported_paths) != len(set(imported_paths)):
        raise MaterialCatalogSyncError("imported AssetCards contain duplicate engine paths")
    merged_by_path = {card.engine_path: card for card in existing_cards}
    before_unrelated = {
        path: canonical_sha256(card)
        for path, card in merged_by_path.items()
        if path not in set(imported_paths)
    }
    for card in imported_cards:
        merged_by_path[card.engine_path] = card
    merged = sorted(merged_by_path.values(), key=lambda card: card.engine_path.casefold())
    ids = [card.asset_id for card in merged]
    if len(ids) != len(set(ids)):
        raise MaterialCatalogSyncError("merged AssetCards contain duplicate asset IDs")
    after_unrelated = {
        card.engine_path: canonical_sha256(card)
        for card in merged
        if card.engine_path in before_unrelated
    }
    if after_unrelated != before_unrelated:
        raise MaterialCatalogSyncError("merge changed an unrelated existing AssetCard")
    return merged


def load_and_merge_asset_catalog(
    catalog_path: str | Path,
    imported_cards: list[AssetCard],
) -> tuple[list[AssetCard], list[AssetCard]]:
    existing = load_asset_card_catalog(catalog_path)
    return existing, merge_asset_cards(existing, imported_cards)


def commit_asset_catalog(
    catalog_path: str | Path,
    cards: list[AssetCard],
) -> Path:
    """Atomically replace a catalog after retaining a timestamped byte-for-byte backup."""

    path = Path(catalog_path).expanduser().resolve()
    if not path.is_file():
        raise MaterialCatalogSyncError(f"asset catalog does not exist: {path}")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.stem}.pre-import-{timestamp}{path.suffix}")
    temporary = path.with_name(f".{path.name}.{timestamp}.tmp")
    text = json.dumps(
        [card.model_dump(mode="json") for card in cards],
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    try:
        shutil.copy2(path, backup)
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        if temporary.exists():
            temporary.unlink()
        raise MaterialCatalogSyncError(f"cannot atomically update asset catalog: {exc}") from exc
    return backup


def catalog_sync_evidence(
    *,
    catalog_path: str | Path,
    backup_path: str | Path,
    raw_scan_path: str | Path,
    before_count: int,
    after_count: int,
    imported_cards: list[AssetCard],
    index_report: AssetIndexReport,
) -> MaterialCatalogSyncEvidence:
    return MaterialCatalogSyncEvidence(
        catalog_path=str(Path(catalog_path).expanduser().resolve()),
        backup_path=str(Path(backup_path).expanduser().resolve()),
        raw_scan_path=str(Path(raw_scan_path).expanduser().resolve()),
        before_count=before_count,
        after_count=after_count,
        imported_asset_ids=[card.asset_id for card in imported_cards],
        chroma_collection=index_report.collection,
        embedding_model=index_report.embedding_model,
        index_inserted=index_report.inserted,
        index_updated=index_report.updated,
        index_deleted=index_report.deleted,
        synced_at=datetime.now(UTC),
    )

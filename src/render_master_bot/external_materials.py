"""Trusted external PBR discovery and acquisition metadata."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import Field

from render_master_bot.models import StrictModel


POLYHAVEN_API_BASE = "https://api.polyhaven.com"
POLYHAVEN_SITE = "https://polyhaven.com"
POLYHAVEN_LICENSE = "CC0-1.0"
POLYHAVEN_LICENSE_URL = "https://polyhaven.com/license"
POLYHAVEN_API_TERMS_URL = "https://polyhaven.com/our-api"
POLYHAVEN_USER_AGENT = "RenderMasterBot/0.1 (Graphics material assistant)"


class ExternalMaterialError(RuntimeError):
    """Raised when external material evidence is incomplete or untrusted."""


class ExternalMaterialCandidate(StrictModel):
    schema_version: str = "0.1"
    provider: str = "polyhaven"
    provider_asset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,199}$")
    display_name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    category: str = Field(min_length=1, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=128)
    authors: list[str] = Field(default_factory=list, max_length=32)
    thumbnail_url: str = Field(min_length=1, max_length=1000)
    asset_url: str = Field(min_length=1, max_length=1000)
    files_api_url: str = Field(min_length=1, max_length=1000)
    files_hash: str | None = Field(default=None, max_length=128)
    license: str = POLYHAVEN_LICENSE
    license_url: str = POLYHAVEN_LICENSE_URL
    api_terms_url: str = POLYHAVEN_API_TERMS_URL
    api_attribution_required: bool = True
    discovered_at: datetime
    similarity: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)


class ExternalMaterialSearchReport(StrictModel):
    schema_version: str = "0.1"
    query: str = Field(min_length=1, max_length=4000)
    provider: str = "polyhaven"
    embedding_model: str = Field(min_length=1, max_length=240)
    provider_credit: str = "Powered by Poly Haven"
    candidates: list[ExternalMaterialCandidate] = Field(max_length=10)


class AcquiredPbrMap(StrictModel):
    role: str = Field(pattern=r"^(base_color|normal|roughness|ambient_occlusion)$")
    source_url: str = Field(min_length=1, max_length=1000)
    provider_md5: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_size: int = Field(gt=0, le=100_000_000)
    downloaded_size: int = Field(gt=0, le=100_000_000)
    local_path: str = Field(min_length=1, max_length=1000)


class ExternalMaterialAcquisition(StrictModel):
    schema_version: str = "0.1"
    status: str = "ready_for_import"
    candidate: ExternalMaterialCandidate
    resolution: str = Field(pattern=r"^(1k|2k|4k|8k)$")
    image_format: str = Field(pattern=r"^(jpg|png)$")
    files_manifest_url: str = Field(min_length=1, max_length=1000)
    files_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    maps: list[AcquiredPbrMap] = Field(min_length=4, max_length=4)
    acquired_at: datetime


_ROLE_KEYS = {
    "base_color": "Diffuse",
    "normal": "nor_dx",
    "roughness": "Rough",
    "ambient_occlusion": "AO",
}
_MD5 = re.compile(r"^[a-f0-9]{32}$")


class TextEmbedder(Protocol):
    def embed_texts(self, *, model: str, texts: list[str]) -> list[list[float]]: ...


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ExternalMaterialError("external material embedding dimensions do not match")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ExternalMaterialError("external material embedding has zero magnitude")
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def _document(asset_id: str, value: dict[str, Any]) -> str:
    tags = value.get("tags") or []
    attributes = value.get("attributes") or {}
    attribute_text = ", ".join(
        f"{key}: {item}" for key, item in sorted(attributes.items())
    )
    return "\n".join([
        f"Asset ID: {asset_id}",
        f"Name: {value.get('name', asset_id)}",
        f"Description: {value.get('description', '')}",
        f"Category: {value.get('category', 'Uncategorized')}",
        "Tags: " + ", ".join(str(tag) for tag in tags),
        f"Attributes: {attribute_text}",
        "Asset type: PBR surface texture material",
    ])


def discover_polyhaven_materials(
    *,
    query: str,
    embedder: TextEmbedder,
    embedding_model: str,
    limit: int = 5,
    timeout_seconds: float = 30.0,
    assets_payload: dict[str, Any] | None = None,
) -> ExternalMaterialSearchReport:
    """Rank official Poly Haven texture assets with local embeddings."""

    request = query.strip()
    if not request:
        raise ExternalMaterialError("external material query cannot be empty")
    if not 1 <= limit <= 10:
        raise ExternalMaterialError("external material search limit must be between 1 and 10")
    if assets_payload is None:
        try:
            response = httpx.get(
                f"{POLYHAVEN_API_BASE}/assets",
                headers={"User-Agent": POLYHAVEN_USER_AGENT},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            assets_payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalMaterialError(f"Poly Haven asset discovery failed: {exc}") from exc
    if not isinstance(assets_payload, dict):
        raise ExternalMaterialError("Poly Haven assets response must be an object")

    textures: list[tuple[str, dict[str, Any]]] = []
    for asset_id, value in assets_payload.items():
        if not isinstance(asset_id, str) or not isinstance(value, dict):
            continue
        if value.get("type") == 1:
            textures.append((asset_id, value))
    if not textures:
        raise ExternalMaterialError("Poly Haven returned no texture assets")
    textures.sort(key=lambda item: item[0])
    documents = [_document(asset_id, value) for asset_id, value in textures]
    vectors = embedder.embed_texts(
        model=embedding_model,
        texts=[request, *documents],
    )
    if len(vectors) != len(documents) + 1:
        raise ExternalMaterialError("embedding response count did not match Poly Haven assets")
    scored = [
        (_cosine(vectors[0], vector), asset_id, value)
        for vector, (asset_id, value) in zip(vectors[1:], textures, strict=True)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    now = datetime.now(UTC)
    candidates = []
    for similarity, asset_id, value in scored[:limit]:
        authors = value.get("authors") or {}
        candidate = ExternalMaterialCandidate(
            provider_asset_id=asset_id,
            display_name=str(value.get("name") or asset_id.replace("_", " ").title()),
            description=str(value.get("description") or f"Poly Haven texture {asset_id}"),
            category=str(value.get("category") or "Uncategorized"),
            tags=[str(tag)[:200] for tag in (value.get("tags") or [])[:128]],
            authors=[str(name)[:200] for name in list(authors)[:32]],
            thumbnail_url=str(value.get("thumbnail_url") or f"{POLYHAVEN_SITE}/a/{asset_id}"),
            asset_url=f"{POLYHAVEN_SITE}/a/{asset_id}",
            files_api_url=f"{POLYHAVEN_API_BASE}/files/{asset_id}",
            files_hash=(str(value["files_hash"]) if value.get("files_hash") else None),
            discovered_at=now,
            similarity=similarity,
        )
        candidates.append(candidate)
    return ExternalMaterialSearchReport(
        query=request,
        embedding_model=embedding_model,
        candidates=candidates,
    )


def load_external_material_search(path: str | Path) -> ExternalMaterialSearchReport:
    try:
        return ExternalMaterialSearchReport.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except Exception as exc:
        raise ExternalMaterialError(f"invalid external material search report: {exc}") from exc


def _verified_file_entry(
    payload: dict[str, Any],
    *,
    role: str,
    resolution: str,
    image_format: str,
) -> tuple[str, int, str]:
    try:
        entry = payload[_ROLE_KEYS[role]][resolution][image_format]
        url = str(entry["url"])
        size = int(entry["size"])
        md5 = str(entry["md5"]).casefold()
    except (KeyError, TypeError, ValueError) as exc:
        raise ExternalMaterialError(
            f"Poly Haven files manifest lacks {role} {resolution} {image_format}"
        ) from exc
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "dl.polyhaven.org":
        raise ExternalMaterialError(f"untrusted Poly Haven download URL: {url}")
    if not 0 < size <= 100_000_000:
        raise ExternalMaterialError(f"unsafe expected download size for {role}: {size}")
    if not _MD5.fullmatch(md5):
        raise ExternalMaterialError(f"invalid provider MD5 for {role}")
    return url, size, md5


def _download_verified_map(
    *,
    role: str,
    url: str,
    expected_size: int,
    expected_md5: str,
    destination: Path,
    timeout_seconds: float,
    download_payloads: dict[str, bytes] | None,
) -> AcquiredPbrMap:
    if destination.exists():
        content = destination.read_bytes()
    elif download_payloads is not None:
        try:
            content = download_payloads[url]
        except KeyError as exc:
            raise ExternalMaterialError(f"test download payload missing: {url}") from exc
    else:
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": POLYHAVEN_USER_AGENT},
                timeout=timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            content = response.content
        except httpx.HTTPError as exc:
            raise ExternalMaterialError(f"Poly Haven map download failed: {exc}") from exc
    if len(content) != expected_size:
        raise ExternalMaterialError(
            f"download size mismatch for {role}: expected {expected_size}, got {len(content)}"
        )
    actual_md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()
    if actual_md5 != expected_md5:
        raise ExternalMaterialError(
            f"provider MD5 mismatch for {role}: expected {expected_md5}, got {actual_md5}"
        )
    sha256 = hashlib.sha256(content).hexdigest()
    if not destination.exists():
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    return AcquiredPbrMap(
        role=role,
        source_url=url,
        provider_md5=expected_md5,
        source_sha256=sha256,
        expected_size=expected_size,
        downloaded_size=len(content),
        local_path=str(destination.resolve()),
    )


def acquire_polyhaven_material(
    candidate: ExternalMaterialCandidate,
    *,
    destination_root: str | Path,
    resolution: str = "1k",
    image_format: str = "jpg",
    timeout_seconds: float = 60.0,
    files_payload: dict[str, Any] | None = None,
    download_payloads: dict[str, bytes] | None = None,
) -> ExternalMaterialAcquisition:
    """Download four importable PBR maps with provider and host hashes."""

    if candidate.provider != "polyhaven" or candidate.license != POLYHAVEN_LICENSE:
        raise ExternalMaterialError("only verified CC0 Poly Haven candidates are supported")
    if resolution not in {"1k", "2k", "4k", "8k"}:
        raise ExternalMaterialError("resolution must be 1k, 2k, 4k, or 8k")
    if image_format not in {"jpg", "png"}:
        raise ExternalMaterialError("image format must be jpg or png")
    expected_manifest_url = f"{POLYHAVEN_API_BASE}/files/{candidate.provider_asset_id}"
    if candidate.files_api_url != expected_manifest_url:
        raise ExternalMaterialError("candidate files endpoint is not the trusted provider URL")
    if files_payload is None:
        try:
            response = httpx.get(
                expected_manifest_url,
                headers={"User-Agent": POLYHAVEN_USER_AGENT},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            files_payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalMaterialError(f"Poly Haven files manifest failed: {exc}") from exc
    if not isinstance(files_payload, dict):
        raise ExternalMaterialError("Poly Haven files manifest must be an object")
    manifest_bytes = json.dumps(
        files_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    destination = (
        Path(destination_root).expanduser().resolve()
        / "polyhaven"
        / candidate.provider_asset_id
        / resolution
    )
    destination.mkdir(parents=True, exist_ok=True)
    maps = []
    for role in _ROLE_KEYS:
        url, size, md5 = _verified_file_entry(
            files_payload,
            role=role,
            resolution=resolution,
            image_format=image_format,
        )
        suffix = ".jpg" if image_format == "jpg" else ".png"
        local_path = destination / f"T_PH_{candidate.provider_asset_id}_{role}_{resolution}{suffix}"
        maps.append(_download_verified_map(
            role=role,
            url=url,
            expected_size=size,
            expected_md5=md5,
            destination=local_path,
            timeout_seconds=timeout_seconds,
            download_payloads=download_payloads,
        ))
    return ExternalMaterialAcquisition(
        candidate=candidate,
        resolution=resolution,
        image_format=image_format,
        files_manifest_url=expected_manifest_url,
        files_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        maps=maps,
        acquired_at=datetime.now(UTC),
    )

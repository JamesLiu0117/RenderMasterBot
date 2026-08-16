"""Persistent semantic retrieval for validated AssetCard catalogs."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import AssetCard


COLLECTION_NAME = "render_master_assets_v01"
DOCUMENT_FORMAT_VERSION = "0.1"


class AssetIndexError(RuntimeError):
    """Raised when an asset catalog cannot be indexed or searched safely."""


class TextEmbedder(Protocol):
    def embed_texts(self, *, model: str, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class AssetIndexReport:
    collection: str
    embedding_model: str
    total: int
    inserted: int
    updated: int
    deleted: int


@dataclass(frozen=True)
class AssetSearchHit:
    rank: int
    asset_id: str
    display_name: str
    asset_type: str
    engine_path: str
    distance: float
    similarity: float
    document: str

    def planner_context(self) -> str:
        details = "; ".join(
            line.strip()
            for line in self.document.splitlines()
            if line.strip().startswith(
                ("Description:", "Tags:", "Dimensions cm:", "Material slots:")
            )
        )
        suffix = f"; {details}" if details else ""
        return (
            f"{self.asset_id}: {self.display_name}; type={self.asset_type}; "
            f"unreal_path={self.engine_path}; similarity={self.similarity:.4f}{suffix}"
        )


_TYPE_LABELS = {
    "static_mesh": "static mesh, 静态网格",
    "skeletal_mesh": "skeletal mesh, 骨骼网格",
    "material": "material, 材质",
    "texture": "texture, 纹理",
    "light": "light, 灯光",
    "camera": "camera, 摄像机",
    "animation": "animation, 动画",
    "blueprint": "Blueprint, 蓝图",
    "level": "level or map, 关卡或地图",
    "other": "other Unreal asset, 其他 Unreal 资产",
}
ASSET_TYPES = frozenset(_TYPE_LABELS)


def load_asset_card_catalog(path: str | Path) -> list[AssetCard]:
    """Load a JSON array and validate every item against the AssetCard contract."""

    catalog_path = Path(path)
    try:
        value = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetIndexError(f"cannot read AssetCard catalog {catalog_path}: {exc}") from exc
    if not isinstance(value, list):
        raise AssetIndexError("AssetCard catalog must be a JSON array")
    try:
        cards = [AssetCard.model_validate(item) for item in value]
    except ValidationError as exc:
        raise AssetIndexError(f"invalid AssetCard catalog {catalog_path}: {exc}") from exc
    if not cards:
        raise AssetIndexError("AssetCard catalog cannot be empty")
    ids = [card.asset_id for card in cards]
    duplicates = sorted({asset_id for asset_id in ids if ids.count(asset_id) > 1})
    if duplicates:
        raise AssetIndexError("duplicate AssetCard IDs: " + ", ".join(duplicates))
    return cards


def asset_document(card: AssetCard) -> str:
    """Create stable multilingual retrieval text without changing the public contract."""

    readable_name = card.display_name.replace("_", " ")
    lines = [
        f"Asset name: {card.display_name}",
        f"Readable name: {readable_name}",
        f"Asset type: {_TYPE_LABELS[card.asset_type]}",
        f"Unreal path: {card.engine_path}",
    ]
    if card.description:
        lines.append(f"Description: {card.description}")
    if card.tags:
        lines.append("Tags: " + ", ".join(card.tags))
    if card.dimensions_cm:
        value = card.dimensions_cm
        lines.append(f"Dimensions cm: x={value.x:g}, y={value.y:g}, z={value.z:g}")
    if card.material_slots:
        lines.append("Material slots: " + ", ".join(card.material_slots))
    return "\n".join(lines)


def _metadata(card: AssetCard) -> dict[str, str]:
    return {
        "asset_id": card.asset_id,
        "display_name": card.display_name,
        "asset_type": card.asset_type,
        "engine_path": card.engine_path,
        "schema_version": card.schema_version,
    }


def _validate_embeddings(embeddings: list[list[float]], expected: int) -> None:
    if len(embeddings) != expected:
        raise AssetIndexError(
            f"embedding service returned {len(embeddings)} vectors for {expected} texts"
        )
    dimensions = {len(vector) for vector in embeddings}
    if not dimensions or 0 in dimensions or len(dimensions) != 1:
        raise AssetIndexError("embedding vectors must be non-empty and have one shared dimension")
    if any(not math.isfinite(float(value)) for vector in embeddings for value in vector):
        raise AssetIndexError("embedding vectors contain a non-finite value")


class AssetIndex:
    """A thin testable boundary around one Chroma collection."""

    def __init__(
        self,
        collection: Any,
        embedder: TextEmbedder,
        *,
        embedding_model: str,
        client: Any | None = None,
    ):
        self.collection = collection
        self.embedder = embedder
        self.embedding_model = embedding_model
        self.client = client

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def __enter__(self) -> "AssetIndex":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def sync(self, cards: list[AssetCard]) -> AssetIndexReport:
        if not cards:
            raise AssetIndexError("cannot index an empty AssetCard catalog")
        ordered = sorted(cards, key=lambda card: card.asset_id)
        ids = [card.asset_id for card in ordered]
        if len(ids) != len(set(ids)):
            raise AssetIndexError("cannot index duplicate AssetCard IDs")
        documents = [asset_document(card) for card in ordered]
        metadatas = [_metadata(card) for card in ordered]
        stored = self.collection.get(include=["documents", "metadatas"])
        stored_ids = [str(value) for value in (stored.get("ids") or [])]
        stored_documents = stored.get("documents") or []
        stored_metadatas = stored.get("metadatas") or []
        if not (
            len(stored_ids) == len(stored_documents) == len(stored_metadatas)
        ):
            raise AssetIndexError("Chroma returned inconsistent stored asset records")
        existing_state = {
            asset_id: (document, metadata)
            for asset_id, document, metadata in zip(
                stored_ids,
                stored_documents,
                stored_metadatas,
                strict=True,
            )
        }
        existing = set(stored_ids)
        current = set(ids)
        stale = sorted(existing - current)
        dirty_indexes = [
            index
            for index, asset_id in enumerate(ids)
            if existing_state.get(asset_id) != (documents[index], metadatas[index])
        ]
        dirty_ids = [ids[index] for index in dirty_indexes]
        if dirty_indexes:
            dirty_documents = [documents[index] for index in dirty_indexes]
            embeddings = self.embedder.embed_texts(
                model=self.embedding_model,
                texts=dirty_documents,
            )
            _validate_embeddings(embeddings, len(dirty_documents))
            self.collection.upsert(
                ids=dirty_ids,
                embeddings=embeddings,
                metadatas=[metadatas[index] for index in dirty_indexes],
                documents=dirty_documents,
            )
        if stale:
            self.collection.delete(ids=stale)
        return AssetIndexReport(
            collection=self.collection.name,
            embedding_model=self.embedding_model,
            total=len(ids),
            inserted=len(current - existing),
            updated=len(set(dirty_ids) & existing),
            deleted=len(stale),
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        asset_types: list[str] | None = None,
    ) -> list[AssetSearchHit]:
        text = query.strip()
        if not text:
            raise AssetIndexError("asset search query cannot be empty")
        if not 1 <= limit <= 100:
            raise AssetIndexError("asset search limit must be between 1 and 100")
        selected_types = sorted(set(asset_types or []))
        invalid_types = sorted(set(selected_types) - ASSET_TYPES)
        if invalid_types:
            raise AssetIndexError("unknown asset types: " + ", ".join(invalid_types))
        count = int(self.collection.count())
        if count == 0:
            return []
        embeddings = self.embedder.embed_texts(model=self.embedding_model, texts=[text])
        _validate_embeddings(embeddings, 1)
        query_arguments: dict[str, Any] = {
            "query_embeddings": embeddings,
            "n_results": min(limit, count),
            "include": ["metadatas", "documents", "distances"],
        }
        if len(selected_types) == 1:
            query_arguments["where"] = {"asset_type": selected_types[0]}
        elif selected_types:
            query_arguments["where"] = {"asset_type": {"$in": selected_types}}
        result = self.collection.query(
            **query_arguments,
        )
        ids = (result.get("ids") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[AssetSearchHit] = []
        for rank, (asset_id, metadata, document, distance) in enumerate(
            zip(ids, metadatas, documents, distances, strict=True),
            start=1,
        ):
            distance_value = float(distance)
            hits.append(
                AssetSearchHit(
                    rank=rank,
                    asset_id=str(asset_id),
                    display_name=str(metadata["display_name"]),
                    asset_type=str(metadata["asset_type"]),
                    engine_path=str(metadata["engine_path"]),
                    distance=distance_value,
                    similarity=max(-1.0, min(1.0, 1.0 - distance_value)),
                    document=str(document),
                )
            )
        return hits


def open_persistent_asset_index(
    path: str | Path,
    embedder: TextEmbedder,
    *,
    embedding_model: str,
    collection_name: str = COLLECTION_NAME,
) -> AssetIndex:
    """Open a cosine Chroma collection and enforce its embedding identity."""

    try:
        import chromadb
    except ImportError as exc:
        raise AssetIndexError(
            "Chroma is not installed in this Python environment; install project dependencies"
        ) from exc

    database_path = Path(path).expanduser().resolve()
    client = None
    try:
        database_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(database_path))
        expected_metadata = {
            "embedding_model": embedding_model,
            "asset_schema_version": "0.1",
            "document_format_version": DOCUMENT_FORMAT_VERSION,
        }
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata=expected_metadata,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
        )
    except Exception as exc:
        if client is not None:
            client.close()
        raise AssetIndexError(f"cannot open Chroma database at {database_path}: {exc}") from exc

    actual_metadata = collection.metadata or {}
    actual_model = actual_metadata.get("embedding_model")
    if actual_model != embedding_model:
        client.close()
        raise AssetIndexError(
            f"collection {collection_name!r} uses embedding model {actual_model!r}, "
            f"not {embedding_model!r}; use a new collection or rebuild the database"
        )
    return AssetIndex(
        collection,
        embedder,
        embedding_model=embedding_model,
        client=client,
    )

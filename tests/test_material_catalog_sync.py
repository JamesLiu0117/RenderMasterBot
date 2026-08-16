import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.contracts import AssetCard, SourceReference
from render_master_bot.external_materials import (
    AcquiredPbrMap,
    ExternalMaterialAcquisition,
    ExternalMaterialCandidate,
)
from render_master_bot.material_catalog_sync import (
    MaterialCatalogSyncError,
    commit_asset_catalog,
    enrich_imported_asset_cards,
    merge_asset_cards,
)
from render_master_bot.serialization import canonical_sha256
from render_master_bot.unreal_materials import PbrMaterialImportResult


def fixtures():
    candidate = ExternalMaterialCandidate(
        provider_asset_id="wood_planks",
        display_name="Wood Planks",
        description="Weathered brown boards.",
        category="Wood",
        tags=["boards", "weathered"],
        thumbnail_url="https://cdn.polyhaven.com/wood.png",
        asset_url="https://polyhaven.com/a/wood_planks",
        files_api_url="https://api.polyhaven.com/files/wood_planks",
        discovered_at=datetime.now(UTC),
        similarity=0.9,
    )
    maps = [
        AcquiredPbrMap(
            role=role,
            source_url=f"https://dl.polyhaven.org/{role}.jpg",
            provider_md5="a" * 32,
            source_sha256=character * 64,
            expected_size=10,
            downloaded_size=10,
            local_path=f"C:/materials/{role}.jpg",
        )
        for role, character in zip(
            ("base_color", "normal", "roughness", "ambient_occlusion"),
            "abcd",
            strict=True,
        )
    ]
    acquisition = ExternalMaterialAcquisition(
        candidate=candidate,
        resolution="1k",
        image_format="jpg",
        files_manifest_url=candidate.files_api_url,
        files_manifest_sha256="e" * 64,
        maps=maps,
        acquired_at=datetime.now(UTC),
    )
    destination = "/Game/Imported/Wood"
    textures = [
        {
            "role": value.role,
            "source_sha256": value.source_sha256,
            "engine_path": f"{destination}/T_{value.role}",
            "class_name": "Texture2D",
            "srgb": value.role == "base_color",
            "compression_settings": "test",
        }
        for value in maps
    ]
    result = PbrMaterialImportResult(
        status="succeeded",
        project_name="Project",
        destination_path=destination,
        material_name="M_Wood",
        material_engine_path=f"{destination}/M_Wood",
        textures=textures,
    )
    source = SourceReference(
        source_id="project_registry",
        source_type="generated",
        title="Project registry",
    )
    cards = [
        AssetCard(
            asset_id=f"imported_{index}",
            engine_path=engine_path,
            display_name=engine_path.rsplit("/", 1)[-1],
            asset_type="material" if engine_path.endswith("M_Wood") else "texture",
            sources=[source],
        )
        for index, engine_path in enumerate(
            [result.material_engine_path, *(value["engine_path"] for value in textures)],
            start=1,
        )
    ]
    return acquisition, result, cards


class MaterialCatalogSyncTests(unittest.TestCase):
    def test_enrichment_attaches_cc0_provenance_to_exact_five_assets(self):
        acquisition, result, cards = fixtures()
        enriched = enrich_imported_asset_cards(
            cards,
            acquisition=acquisition,
            import_result=result,
        )

        self.assertEqual(len(enriched), 5)
        self.assertTrue(all(card.license == "CC0-1.0" for card in enriched))
        self.assertTrue(all(len(card.sources) == 2 for card in enriched))
        material = next(card for card in enriched if card.asset_type == "material")
        self.assertIn("Weathered brown boards", material.description)
        self.assertIn("polyhaven", material.tags)

    def test_enrichment_rejects_an_incomplete_scan(self):
        acquisition, result, cards = fixtures()
        with self.assertRaisesRegex(MaterialCatalogSyncError, "exact five"):
            enrich_imported_asset_cards(
                cards[:-1],
                acquisition=acquisition,
                import_result=result,
            )

    def test_merge_preserves_unrelated_cards_and_replaces_same_engine_path(self):
        acquisition, result, cards = fixtures()
        imported = enrich_imported_asset_cards(
            cards,
            acquisition=acquisition,
            import_result=result,
        )
        unrelated = AssetCard(
            asset_id="existing_cube",
            engine_path="/Game/Existing/SM_Cube",
            display_name="SM_Cube",
            asset_type="static_mesh",
        )
        old_material = cards[0].model_copy(update={"description": "old"})
        merged = merge_asset_cards([unrelated, old_material], imported)

        self.assertEqual(len(merged), 6)
        observed = next(card for card in merged if card.asset_id == unrelated.asset_id)
        self.assertEqual(canonical_sha256(observed), canonical_sha256(unrelated))
        material = next(card for card in merged if card.engine_path == result.material_engine_path)
        self.assertEqual(material.license, "CC0-1.0")

    def test_commit_creates_backup_before_atomic_catalog_replace(self):
        _, _, cards = fixtures()
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "asset_cards.json"
            original = cards[0].model_dump_json(indent=2)
            catalog.write_text(f"[{original}]\n", encoding="utf-8")
            backup = commit_asset_catalog(catalog, cards)

            self.assertTrue(backup.is_file())
            self.assertEqual(backup.read_text(encoding="utf-8"), f"[{original}]\n")
            self.assertEqual(len(__import__("json").loads(catalog.read_text())), 5)


if __name__ == "__main__":
    unittest.main()

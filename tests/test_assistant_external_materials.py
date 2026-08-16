import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from render_master_bot.assistant_external_materials import (
    _assistant_import_names,
    prepare_external_material_assistant_proposal,
)
from render_master_bot.external_materials import (
    AcquiredPbrMap,
    ExternalMaterialAcquisition,
    ExternalMaterialCandidate,
    ExternalMaterialSearchReport,
)


class ExternalMaterialAssistantTests(unittest.TestCase):
    def test_provider_id_maps_to_bounded_unreal_names(self):
        candidate = ExternalMaterialCandidate(
            provider_asset_id="planks_brown_10",
            display_name="Brown Planks 10",
            description="Brown wood planks",
            category="Wood",
            thumbnail_url="https://cdn.polyhaven.com/planks.jpg",
            asset_url="https://polyhaven.com/a/planks_brown_10",
            files_api_url="https://api.polyhaven.com/files/planks_brown_10",
            discovered_at=datetime.now(UTC),
            similarity=0.8,
        )
        self.assertEqual(
            _assistant_import_names(candidate),
            (
                "/Game/RenderMasterBot/Imported/PolyHaven/PlanksBrown10",
                "M_PH_PlanksBrown10",
            ),
        )

    def test_prepare_freezes_verified_download_as_reviewable_proposal(self):
        candidate = ExternalMaterialCandidate(
            provider_asset_id="wood_planks",
            display_name="Wood Planks",
            description="Weathered boards",
            category="Wood",
            thumbnail_url="https://cdn.polyhaven.com/wood.jpg",
            asset_url="https://polyhaven.com/a/wood_planks",
            files_api_url="https://api.polyhaven.com/files/wood_planks",
            discovered_at=datetime.now(UTC),
            similarity=0.9,
        )
        search = ExternalMaterialSearchReport(
            query="weathered wood",
            embedding_model="embed",
            candidates=[candidate],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            maps = []
            for role, character in zip(
                ("base_color", "normal", "roughness", "ambient_occlusion"),
                "abcd",
                strict=True,
            ):
                path = root / f"{role}.jpg"
                path.write_bytes(role.encode())
                maps.append(AcquiredPbrMap(
                    role=role,
                    source_url=f"https://dl.polyhaven.org/{role}.jpg",
                    provider_md5="0" * 32,
                    source_sha256=__import__("hashlib").sha256(path.read_bytes()).hexdigest(),
                    expected_size=path.stat().st_size,
                    downloaded_size=path.stat().st_size,
                    local_path=str(path),
                ))
            acquisition = ExternalMaterialAcquisition(
                candidate=candidate,
                resolution="1k",
                image_format="jpg",
                files_manifest_url=candidate.files_api_url,
                files_manifest_sha256="e" * 64,
                maps=maps,
                acquired_at=datetime.now(UTC),
            )
            with patch(
                "render_master_bot.assistant_external_materials.discover_polyhaven_materials",
                return_value=search,
            ), patch(
                "render_master_bot.assistant_external_materials.acquire_polyhaven_material",
                return_value=acquisition,
            ):
                _, _, proposal, assistant = prepare_external_material_assistant_proposal(
                    query="weathered wood",
                    embedder=object(),
                    embedding_model="embed",
                    library_root=root / "library",
                    work_directory=root / "work",
                    proposal_id="external_wood_001",
                )

            self.assertEqual(assistant.status, "pending_approval")
            self.assertEqual(assistant.proposal_id, "external_wood_001")
            self.assertEqual(assistant.downloaded_map_count, 4)
            self.assertEqual(assistant.planned_asset_paths, proposal.planned_asset_paths)
            self.assertTrue(Path(assistant.import_proposal_path).is_file())
            self.assertFalse((root / "Content").exists())


if __name__ == "__main__":
    unittest.main()

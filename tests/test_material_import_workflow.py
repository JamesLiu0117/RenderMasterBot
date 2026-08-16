import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from render_master_bot.external_materials import (
    AcquiredPbrMap,
    ExternalMaterialAcquisition,
    ExternalMaterialCandidate,
)
from render_master_bot.material_import_workflow import (
    MaterialImportWorkflowError,
    create_external_material_import_proposal,
    execute_external_material_import,
)
from render_master_bot.serialization import canonical_sha256
from render_master_bot.unreal_materials import PbrMaterialImportResult


def write_acquisition(root: Path) -> Path:
    candidate = ExternalMaterialCandidate(
        provider_asset_id="wood_planks",
        display_name="Wood Planks",
        description="Weathered wood",
        category="Wood",
        thumbnail_url="https://cdn.polyhaven.com/wood.png",
        asset_url="https://polyhaven.com/a/wood_planks",
        files_api_url="https://api.polyhaven.com/files/wood_planks",
        discovered_at=datetime.now(UTC),
        similarity=0.9,
    )
    maps = []
    for role in ("base_color", "normal", "roughness", "ambient_occlusion"):
        path = root / f"T_Wood_{role}.jpg"
        path.write_bytes(f"map:{role}".encode())
        maps.append(AcquiredPbrMap(
            role=role,
            source_url=f"https://dl.polyhaven.org/{role}.jpg",
            provider_md5="a" * 32,
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
        files_manifest_sha256="b" * 64,
        maps=maps,
        acquired_at=datetime.now(UTC),
    )
    path = root / "acquisition.json"
    path.write_text(acquisition.model_dump_json(), encoding="utf-8")
    return path


class MaterialImportWorkflowTests(unittest.TestCase):
    def test_proposal_freezes_acquisition_and_lists_exact_five_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            acquisition_path = write_acquisition(root)
            proposal = create_external_material_import_proposal(
                acquisition_path=acquisition_path,
                destination_path="/Game/RenderMasterBot/Imported/Wood",
                material_name="M_PH_Wood",
                proposal_id="external_wood_001",
            )

        self.assertEqual(proposal.status, "pending_approval")
        self.assertTrue(proposal.approval_required)
        self.assertEqual(len(proposal.planned_asset_paths), 5)
        self.assertEqual(proposal.license, "CC0-1.0")

    def test_execution_refuses_wrong_approval_hash_before_unreal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            acquisition_path = write_acquisition(root)
            proposal = create_external_material_import_proposal(
                acquisition_path=acquisition_path,
                destination_path="/Game/Imported/Wood",
                material_name="M_PH_Wood",
                proposal_id="external_wood_001",
            )
            proposal_path = root / "proposal.json"
            proposal_path.write_text(proposal.model_dump_json(), encoding="utf-8")
            with patch(
                "render_master_bot.material_import_workflow.run_unreal_pbr_material_import"
            ) as run:
                with self.assertRaisesRegex(MaterialImportWorkflowError, "approval SHA-256"):
                    execute_external_material_import(
                        proposal_path,
                        approved_proposal_sha256="0" * 64,
                        approved_by="tester",
                        uproject_path=root / "Project.uproject",
                        engine_root=root / "UE",
                        import_output=root / "import.json",
                    )
            run.assert_not_called()

    def test_execution_binds_approval_and_import_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            acquisition_path = write_acquisition(root)
            proposal = create_external_material_import_proposal(
                acquisition_path=acquisition_path,
                destination_path="/Game/Imported/Wood",
                material_name="M_PH_Wood",
                proposal_id="external_wood_001",
            )
            proposal_path = root / "proposal.json"
            proposal_path.write_text(proposal.model_dump_json(), encoding="utf-8")
            result = PbrMaterialImportResult(
                status="succeeded",
                project_name="Project",
                destination_path=proposal.destination_path,
                material_name=proposal.material_name,
                material_engine_path=f"{proposal.destination_path}/{proposal.material_name}",
                textures=[{
                    "role": role,
                    "source_sha256": "a" * 64,
                    "engine_path": f"{proposal.destination_path}/T_{role}",
                    "class_name": "Texture2D",
                    "srgb": role == "base_color",
                    "compression_settings": "test",
                } for role in ("base_color", "normal", "roughness", "ambient_occlusion")],
            )
            with patch(
                "render_master_bot.material_import_workflow.run_unreal_pbr_material_import",
                return_value=(object(), result),
            ):
                execution = execute_external_material_import(
                    proposal_path,
                    approved_proposal_sha256=canonical_sha256(proposal),
                    approved_by="tester",
                    uproject_path=root / "Project.uproject",
                    engine_root=root / "UE",
                    import_output=root / "import.json",
                )

        self.assertEqual(execution.status, "succeeded")
        self.assertEqual(execution.approval.proposal_sha256, execution.proposal_sha256)


if __name__ == "__main__":
    unittest.main()

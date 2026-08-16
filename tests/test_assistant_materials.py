import json
import tempfile
import unittest
from pathlib import Path

from render_master_bot.asset_index import AssetSearchHit
from render_master_bot.assistant_materials import (
    MaterialProposalError,
    propose_material_change,
)
from render_master_bot.contracts import UnrealSelectionContext


def selection_context(
    *,
    slots: int = 1,
    target_slot_index: int | None = None,
) -> UnrealSelectionContext:
    value = {
        "project_name": "OptimizationPlugin",
        "level_path": "/Game/FirstPerson/Lvl_FirstPerson",
        "actor_name": "DoorActor",
        "actor_path": "/Game/FirstPerson/Lvl_FirstPerson.DoorActor",
        "component_name": "StaticMeshComponent0",
        "mesh_path": "/Game/Props/SM_Door.SM_Door",
        "material_slots": [
            {
                "slot_index": index,
                "slot_name": f"Material_{index}",
                "current_material_path": (
                    "/Game/Materials/M_Default.M_Default" if index == 0 else None
                ),
            }
            for index in range(slots)
        ],
    }
    if target_slot_index is not None:
        value["target_slot_index"] = target_slot_index
    return UnrealSelectionContext.model_validate(value)


def material_card(asset_id: str, engine_path: str, display_name: str) -> dict:
    return {
        "asset_id": asset_id,
        "engine_path": engine_path,
        "display_name": display_name,
        "asset_type": "material",
    }


def hit(rank: int, asset_id: str, engine_path: str, similarity: float) -> AssetSearchHit:
    return AssetSearchHit(
        rank=rank,
        asset_id=asset_id,
        display_name=asset_id,
        asset_type="material",
        engine_path=engine_path,
        distance=1.0 - similarity,
        similarity=similarity,
        document=f"Asset name: {asset_id}",
    )


class FakeSearcher:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query, *, limit=5, asset_types=None):
        self.calls.append((query, limit, asset_types))
        return self.hits


class AssistantMaterialTests(unittest.TestCase):
    def write_catalog(self, directory: str, cards: list[dict]) -> Path:
        path = Path(directory) / "asset_cards.json"
        path.write_text(json.dumps(cards), encoding="utf-8")
        return path

    def test_proposal_uses_catalog_verified_material_and_excludes_current(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = self.write_catalog(directory, [
                material_card(
                    "default_material",
                    "/Game/Materials/M_Default.M_Default",
                    "Default Material",
                ),
                material_card(
                    "weathered_wood",
                    "/Game/Materials/M_Weathered.M_Weathered",
                    "Weathered Wood",
                ),
            ])
            searcher = FakeSearcher([
                hit(1, "default_material", "/Game/Materials/M_Default.M_Default", 0.91),
                hit(2, "weathered_wood", "/Game/Materials/M_Weathered.M_Weathered", 0.82),
            ])

            proposal = propose_material_change(
                prompt="make the selected door look old and weathered",
                context=selection_context(),
                asset_catalog_path=catalog,
                searcher=searcher,
                embedding_model="embedding-test",
                proposal_id="proposal_001",
            )

        self.assertEqual(proposal.status, "proposed")
        self.assertEqual(proposal.selected_material.asset_id, "weathered_wood")
        self.assertEqual(proposal.selected_slot.slot_index, 0)
        self.assertTrue(proposal.modifies_editor_scene)
        self.assertFalse(proposal.auto_save)
        self.assertTrue(proposal.undo_supported)
        self.assertEqual(searcher.calls, [
            ("make the selected door look old and weathered", 5, ["material"])
        ])

    def test_multiple_slots_stop_instead_of_guessing_a_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = self.write_catalog(directory, [
                material_card("weathered_wood", "/Game/M_Weathered", "Weathered Wood")
            ])
            searcher = FakeSearcher([])
            proposal = propose_material_change(
                prompt="make it old",
                context=selection_context(slots=2),
                asset_catalog_path=catalog,
                searcher=searcher,
                embedding_model="embedding-test",
            )

        self.assertEqual(proposal.status, "unresolved")
        self.assertIn("explicit multi-slot material targeting", proposal.missing_capabilities)
        self.assertEqual(searcher.calls, [])

    def test_explicit_target_allows_a_multi_slot_material_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = self.write_catalog(directory, [
                material_card("metal", "/Game/Materials/M_Metal", "Dark Metal")
            ])
            searcher = FakeSearcher([
                hit(1, "metal", "/Game/Materials/M_Metal", 0.88)
            ])
            proposal = propose_material_change(
                prompt="make only the handle dark metal",
                context=selection_context(slots=3, target_slot_index=2),
                asset_catalog_path=catalog,
                searcher=searcher,
                embedding_model="embedding-test",
            )

        self.assertEqual(proposal.status, "proposed")
        self.assertEqual(proposal.target.target_slot_index, 2)
        self.assertEqual(proposal.selected_slot.slot_index, 2)
        self.assertEqual(proposal.selected_slot.slot_name, "Material_2")
        self.assertEqual(proposal.selected_material.asset_id, "metal")

    def test_retrieval_cannot_escape_the_supplied_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = self.write_catalog(directory, [
                material_card("known", "/Game/M_Known", "Known")
            ])
            searcher = FakeSearcher([hit(1, "invented", "/Game/M_Invented", 0.99)])

            with self.assertRaisesRegex(MaterialProposalError, "outside the supplied catalog"):
                propose_material_change(
                    prompt="use an invented material",
                    context=selection_context(),
                    asset_catalog_path=catalog,
                    searcher=searcher,
                    embedding_model="embedding-test",
                )

    def test_no_different_candidate_returns_auditable_unresolved_result(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = self.write_catalog(directory, [
                material_card(
                    "default_material",
                    "/Game/Materials/M_Default.M_Default",
                    "Default Material",
                )
            ])
            searcher = FakeSearcher([
                hit(1, "default_material", "/Game/Materials/M_Default", 0.95)
            ])
            proposal = propose_material_change(
                prompt="keep the existing appearance",
                context=selection_context(),
                asset_catalog_path=catalog,
                searcher=searcher,
                embedding_model="embedding-test",
            )

        self.assertEqual(proposal.status, "unresolved")
        self.assertIn("matching project material asset", proposal.missing_capabilities)


if __name__ == "__main__":
    unittest.main()

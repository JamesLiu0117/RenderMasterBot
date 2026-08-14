import json
import tempfile
import unittest
from pathlib import Path

from render_master_bot.asset_index import (
    AssetIndexError,
    asset_document,
    load_asset_card_catalog,
    open_persistent_asset_index,
)
from render_master_bot.contracts import AssetCard


def card(asset_id: str, name: str, asset_type: str, path: str) -> AssetCard:
    return AssetCard(
        asset_id=asset_id,
        engine_path=path,
        display_name=name,
        asset_type=asset_type,
    )


class SemanticFakeEmbedder:
    def embed_texts(self, *, model: str, texts: list[str]) -> list[list[float]]:
        values = []
        for text in texts:
            folded = text.casefold()
            if "door" in folded or "门" in text:
                values.append([1.0, 0.0, 0.0])
            elif "manny" in folded or "角色" in text:
                values.append([0.0, 1.0, 0.0])
            else:
                values.append([0.0, 0.0, 1.0])
        return values


class AssetIndexTests(unittest.TestCase):
    def test_catalog_loader_validates_every_card(self):
        values = [
            card("sm_door", "SM_Door", "static_mesh", "/Game/Props/SM_Door").model_dump(
                mode="json"
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            path.write_text(json.dumps(values), encoding="utf-8")

            cards = load_asset_card_catalog(path)

        self.assertEqual(cards[0].asset_id, "sm_door")

    def test_document_contains_multilingual_type_and_engine_metadata(self):
        value = card("sm_door", "SM_Door", "static_mesh", "/Game/Props/SM_Door")
        document = asset_document(value)

        self.assertIn("SM Door", document)
        self.assertIn("静态网格", document)
        self.assertIn("/Game/Props/SM_Door", document)

    def test_real_chroma_sync_search_and_stale_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            first = [
                card("sm_door", "SM_Door", "static_mesh", "/Game/Props/SM_Door"),
                card(
                    "skm_manny",
                    "SKM_Manny",
                    "skeletal_mesh",
                    "/Game/Characters/SKM_Manny",
                ),
            ]

            with open_persistent_asset_index(
                directory,
                SemanticFakeEmbedder(),
                embedding_model="fake-embedding",
                collection_name="test_asset_cards",
            ) as index:
                report = index.sync(first)
                hits = index.search("我需要一扇门", limit=2)
                second_report = index.sync(first[:1])
                final_count = index.collection.count()

            self.assertEqual(report.inserted, 2)
            self.assertEqual(hits[0].asset_id, "sm_door")
            self.assertEqual(hits[0].rank, 1)
            self.assertEqual(second_report.updated, 1)
            self.assertEqual(second_report.deleted, 1)
            self.assertEqual(final_count, 1)

    def test_collection_rejects_a_different_embedding_model(self):
        with tempfile.TemporaryDirectory() as directory:
            first = open_persistent_asset_index(
                directory,
                SemanticFakeEmbedder(),
                embedding_model="model-a",
                collection_name="embedding_identity",
            )
            try:
                with self.assertRaisesRegex(AssetIndexError, "uses embedding model"):
                    open_persistent_asset_index(
                        directory,
                        SemanticFakeEmbedder(),
                        embedding_model="model-b",
                        collection_name="embedding_identity",
                    )
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()

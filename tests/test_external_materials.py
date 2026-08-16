import hashlib
import tempfile
import unittest
from datetime import UTC, datetime

from render_master_bot.external_materials import (
    ExternalMaterialError,
    ExternalMaterialCandidate,
    POLYHAVEN_USER_AGENT,
    acquire_polyhaven_material,
    discover_polyhaven_materials,
)


class FakeEmbedder:
    def embed_texts(self, *, model, texts):
        values = []
        for text in texts:
            folded = text.casefold()
            if "dark wood" in folded or "wood" in folded:
                values.append([1.0, 0.0])
            else:
                values.append([0.0, 1.0])
        return values


class ExternalMaterialTests(unittest.TestCase):
    def test_polyhaven_discovery_filters_textures_and_preserves_license_evidence(self):
        payload = {
            "wood_planks": {
                "type": 1,
                "name": "Wood Planks",
                "description": "Dark weathered wood surface",
                "category": "Wood/Planks",
                "tags": ["wood", "weathered"],
                "authors": {"Artist": "All"},
                "thumbnail_url": "https://cdn.polyhaven.com/wood.png",
                "files_hash": "abc123",
            },
            "studio_hdri": {
                "type": 0,
                "name": "Studio",
                "description": "An HDRI",
                "category": "Studio",
            },
        }
        report = discover_polyhaven_materials(
            query="dark wood",
            embedder=FakeEmbedder(),
            embedding_model="fake",
            assets_payload=payload,
        )

        self.assertEqual(len(report.candidates), 1)
        candidate = report.candidates[0]
        self.assertEqual(candidate.provider_asset_id, "wood_planks")
        self.assertEqual(candidate.license, "CC0-1.0")
        self.assertTrue(candidate.api_attribution_required)
        self.assertEqual(report.provider_credit, "Powered by Poly Haven")
        self.assertIn("RenderMasterBot", POLYHAVEN_USER_AGENT)

    def test_discovery_rejects_empty_query_and_missing_texture_assets(self):
        with self.assertRaisesRegex(ExternalMaterialError, "cannot be empty"):
            discover_polyhaven_materials(
                query=" ",
                embedder=FakeEmbedder(),
                embedding_model="fake",
                assets_payload={},
            )
        with self.assertRaisesRegex(ExternalMaterialError, "no texture assets"):
            discover_polyhaven_materials(
                query="wood",
                embedder=FakeEmbedder(),
                embedding_model="fake",
                assets_payload={"sky": {"type": 0}},
            )

    def test_acquisition_verifies_provider_md5_size_and_records_sha256(self):
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
        payload = {}
        downloads = {}
        keys = {
            "base_color": "Diffuse",
            "normal": "nor_dx",
            "roughness": "Rough",
            "ambient_occlusion": "AO",
        }
        for role, key in keys.items():
            content = f"map:{role}".encode()
            url = f"https://dl.polyhaven.org/file/{role}.jpg"
            payload[key] = {"1k": {"jpg": {
                "url": url,
                "size": len(content),
                "md5": hashlib.md5(content, usedforsecurity=False).hexdigest(),
            }}}
            downloads[url] = content

        with tempfile.TemporaryDirectory() as directory:
            acquisition = acquire_polyhaven_material(
                candidate,
                destination_root=directory,
                files_payload=payload,
                download_payloads=downloads,
            )

        self.assertEqual(acquisition.status, "ready_for_import")
        self.assertEqual(len(acquisition.maps), 4)
        self.assertTrue(all(len(value.source_sha256) == 64 for value in acquisition.maps))

    def test_acquisition_rejects_untrusted_download_host(self):
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
        payload = {
            key: {"1k": {"jpg": {
                "url": "https://evil.example/map.jpg",
                "size": 4,
                "md5": "a" * 32,
            }}}
            for key in ("Diffuse", "nor_dx", "Rough", "AO")
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ExternalMaterialError, "untrusted"):
                acquire_polyhaven_material(
                    candidate,
                    destination_root=directory,
                    files_payload=payload,
                    download_payloads={},
                )


if __name__ == "__main__":
    unittest.main()

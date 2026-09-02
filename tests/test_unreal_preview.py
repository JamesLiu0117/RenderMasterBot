import json
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from render_master_bot.contracts import AssetCard, RunManifest
from render_master_bot.models import RenderSpec
from render_master_bot.unreal_preview import UnrealPreviewError, run_unreal_preview


def preview_spec() -> RenderSpec:
    return RenderSpec.model_validate(
        {
            "source_prompt": "Render one door.",
            "scene_name": "door_preview",
            "objects": [
                {
                    "object_id": "door",
                    "asset": {"asset_id": "door_asset"},
                    "transform": {"location_cm": {"z": 100}},
                }
            ],
            "camera": {
                "camera_id": "camera",
                "transform": {"location_cm": {"x": -300, "z": 150}},
            },
            "lights": [
                {
                    "light_id": "sun",
                    "kind": "directional",
                    "intensity": 10_000,
                    "intensity_unit": "lux",
                }
            ],
            "render": {"width_px": 640, "height_px": 360},
        }
    )


def preview_asset() -> AssetCard:
    return AssetCard(
        asset_id="door_asset",
        engine_path="/Game/Props/SM_Door",
        display_name="SM_Door",
        asset_type="static_mesh",
        dimensions_cm={"x": 10, "y": 100, "z": 200},
    )


def prepare_files(root: Path):
    project = root / "Project.uproject"
    project.write_text("{}", encoding="utf-8")
    editor = root / "UE" / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    editor.parent.mkdir(parents=True)
    editor.write_bytes(b"")
    spec = root / "spec.json"
    spec.write_text(preview_spec().model_dump_json(), encoding="utf-8")
    assets = root / "assets.json"
    assets.write_text(
        json.dumps([preview_asset().model_dump(mode="json")]),
        encoding="utf-8",
    )
    return project, root / "UE", spec, assets


def png_bytes(*, visible: bool = True) -> bytes:
    width = 2
    height = 2
    pixels = (
        b"\x00\x00\x00\xff\xff\xff" b"\x40\x20\x10\x80\x80\x80"
        if visible
        else b"\x00" * 12
    )
    raw = b"\x00" + pixels[:6] + b"\x00" + pixels[6:]

    def chunk(kind: bytes, value: bytes) -> bytes:
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def actor_evidence(request: dict) -> list[dict]:
    requested_exposure = request["camera"].get(
        "exposure",
        {"mode": "auto", "fixed_ev100": None},
    )
    actors = [
        {
            "actor_id": item["object_id"],
            "actor_kind": "static_mesh",
            "actor_name": item["object_id"],
            "class_name": "StaticMeshActor",
            "transform": item["transform"],
            "asset_bounds": {
                "dimensions_cm": item["dimensions_cm"],
                "pivot_offset_cm": item["pivot_offset_cm"],
            },
        }
        for item in request["objects"]
    ]
    actors.append(
        {
            "actor_id": request["camera"]["camera_id"],
            "actor_kind": "camera",
            "actor_name": "camera",
            "class_name": "CineCameraActor",
            "transform": request["camera"]["transform"],
            "exposure": {
                "mode": requested_exposure["mode"],
                "fixed_ev100": requested_exposure.get("fixed_ev100"),
                "extended_luminance_range": True,
            },
        }
    )
    actors.extend(
        {
            "actor_id": item["light_id"],
            "actor_kind": item["kind"],
            "actor_name": item["light_id"],
            "class_name": "Light",
            "transform": item["transform"],
        }
        for item in request["lights"]
    )
    return actors


class UnrealPreviewTests(unittest.TestCase):
    def test_success_finalizes_manifest_with_hashed_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, engine, spec, assets = prepare_files(root)
            run_directory = root / "run"
            scratch_preview = None

            def fake_run(command, **kwargs):
                nonlocal scratch_preview
                self.assertIn("-RenderOffscreen", command)
                self.assertIn("-DisablePlugins=RenderMasterBot", command)
                self.assertIn("-DDC-ForceMemoryCache", command)
                self.assertTrue(
                    any(value.startswith("-ShaderWorkingDir=") for value in command)
                )
                request = json.loads(
                    Path(kwargs["env"]["RENDERMASTER_PREVIEW_REQUEST"]).read_text(
                        encoding="utf-8"
                    )
                )
                preview = Path(kwargs["env"]["RENDERMASTER_PREVIEW_DIRECTORY"]) / "beauty.png"
                scratch_preview = preview
                preview.write_bytes(png_bytes())
                result = {
                    "schema_version": "0.1",
                    "status": "succeeded",
                    "project_name": "Project",
                    "world_name": "Map",
                    "scene_name": request["scene_name"],
                    "render_spec_sha256": request["render_spec_sha256"],
                    "actors": actor_evidence(request),
                    "preview_files": [str(preview.resolve())],
                    "warnings": [],
                    "errors": [],
                }
                Path(kwargs["env"]["RENDERMASTER_PREVIEW_RESULT"]).write_text(
                    json.dumps(result),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("render_master_bot.unreal_preview.subprocess.run", side_effect=fake_run):
                manifest, result = run_unreal_preview(
                    project,
                    engine_root=engine,
                    render_spec_path=spec,
                    asset_catalog_path=assets,
                    run_directory=run_directory,
                    run_id="preview_test",
                )

            self.assertEqual(manifest.status, "succeeded")
            self.assertEqual(len(result.preview_files), 1)
            self.assertEqual(
                Path(result.preview_files[0]),
                (run_directory / "preview" / "beauty.png").resolve(),
            )
            self.assertFalse(scratch_preview.exists())
            self.assertEqual(
                {artifact.role for artifact in manifest.output_artifacts},
                {"unreal_result", "beauty_preview", "unreal_process_log"},
            )
            saved = RunManifest.model_validate_json(
                (run_directory / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved.status, "succeeded")
            self.assertTrue(all(artifact.sha256 for artifact in saved.output_artifacts))

    def test_black_preview_is_rejected_even_when_unreal_reports_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, engine, spec, assets = prepare_files(root)
            run_directory = root / "black_run"

            def fake_run(command, **kwargs):
                request = json.loads(
                    Path(kwargs["env"]["RENDERMASTER_PREVIEW_REQUEST"]).read_text(
                        encoding="utf-8"
                    )
                )
                preview = Path(kwargs["env"]["RENDERMASTER_PREVIEW_DIRECTORY"]) / "beauty.png"
                preview.write_bytes(png_bytes(visible=False))
                result = {
                    "schema_version": "0.1",
                    "status": "succeeded",
                    "project_name": "Project",
                    "world_name": "Map",
                    "scene_name": request["scene_name"],
                    "render_spec_sha256": request["render_spec_sha256"],
                    "actors": actor_evidence(request),
                    "preview_files": [str(preview.resolve())],
                    "warnings": [],
                    "errors": [],
                }
                Path(kwargs["env"]["RENDERMASTER_PREVIEW_RESULT"]).write_text(
                    json.dumps(result),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("render_master_bot.unreal_preview.subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(UnrealPreviewError, "no meaningful visible pixels"):
                    run_unreal_preview(
                        project,
                        engine_root=engine,
                        render_spec_path=spec,
                        asset_catalog_path=assets,
                        run_directory=run_directory,
                        run_id="preview_black",
                    )

            saved = RunManifest.model_validate_json(
                (run_directory / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved.status, "failed")
            self.assertIn("no meaningful visible pixels", saved.errors[0])

    def test_subprocess_failure_still_finalizes_failed_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, engine, spec, assets = prepare_files(root)
            run_directory = root / "failed_run"

            with patch(
                "render_master_bot.unreal_preview.subprocess.run",
                return_value=subprocess.CompletedProcess([], 3, "Error: simulated", ""),
            ):
                with self.assertRaisesRegex(UnrealPreviewError, "no preview result"):
                    run_unreal_preview(
                        project,
                        engine_root=engine,
                        render_spec_path=spec,
                        asset_catalog_path=assets,
                        run_directory=run_directory,
                        run_id="preview_failed",
                    )

            saved = RunManifest.model_validate_json(
                (run_directory / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved.status, "failed")
            self.assertTrue(saved.errors)
            self.assertIsNotNone(saved.finished_at)
            self.assertEqual(
                {artifact.role for artifact in saved.output_artifacts},
                {"unreal_process_log"},
            )

    def test_nonempty_run_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, engine, spec, assets = prepare_files(root)
            run_directory = root / "existing"
            run_directory.mkdir()
            marker = run_directory / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(UnrealPreviewError, "not empty"):
                run_unreal_preview(
                    project,
                    engine_root=engine,
                    render_spec_path=spec,
                    asset_catalog_path=assets,
                    run_directory=run_directory,
                    run_id="preview_existing",
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_invalid_run_id_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, engine, spec, assets = prepare_files(root)
            run_directory = root / "invalid"

            with self.assertRaisesRegex(UnrealPreviewError, "invalid run ID"):
                run_unreal_preview(
                    project,
                    engine_root=engine,
                    render_spec_path=spec,
                    asset_catalog_path=assets,
                    run_directory=run_directory,
                    run_id="INVALID ID",
                )
            self.assertFalse(run_directory.exists())


if __name__ == "__main__":
    unittest.main()

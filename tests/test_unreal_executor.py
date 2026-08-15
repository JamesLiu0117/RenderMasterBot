import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from render_master_bot.contracts import AssetCard
from render_master_bot.models import RenderSpec
from render_master_bot.unreal_executor import (
    SpawnedActorRecord,
    UnrealSceneBuildError,
    UnrealSceneBuildResult,
    _validate_observed_result,
    load_scene_build_result,
    resolve_scene_build_request,
    run_unreal_scene_build,
)


def scene_spec(**overrides) -> RenderSpec:
    value = {
        "source_prompt": "Create a simple door product shot.",
        "scene_name": "door_test",
        "objects": [
            {
                "object_id": "door",
                "asset": {"asset_id": "door_asset"},
                "transform": {
                    "location_cm": {"x": 10, "y": 20, "z": 30},
                    "rotation_deg": {"x": 1, "y": 2, "z": 3},
                    "scale": {"x": 1, "y": 1, "z": 1},
                },
            }
        ],
        "camera": {
            "camera_id": "camera",
            "transform": {
                "location_cm": {"x": -300, "y": 0, "z": 150},
                "rotation_deg": {"x": 0, "y": 0, "z": 0},
            },
        },
        "lights": [
            {
                "light_id": "key",
                "kind": "directional",
                "intensity": 10_000,
                "intensity_unit": "lux",
            }
        ],
    }
    value.update(overrides)
    return RenderSpec.model_validate(value)


def asset_card(**overrides) -> AssetCard:
    value = {
        "asset_id": "door_asset",
        "engine_path": "/Game/Props/SM_Door",
        "display_name": "SM_Door",
        "asset_type": "static_mesh",
    }
    value.update(overrides)
    return AssetCard.model_validate(value)


def actor_record(actor_id: str, actor_kind: str, transform=None) -> SpawnedActorRecord:
    return SpawnedActorRecord(
        actor_id=actor_id,
        actor_kind=actor_kind,
        actor_name=f"RMB_{actor_id}",
        class_name="TestActor",
        transform=transform or {},
    )


class UnrealSceneBuildTests(unittest.TestCase):
    def test_resolves_asset_ids_to_bounded_unreal_paths(self):
        request = resolve_scene_build_request(scene_spec(), [asset_card()])

        self.assertEqual(request.scene_name, "door_test")
        self.assertEqual(request.objects[0].asset_id, "door_asset")
        self.assertEqual(request.objects[0].engine_path, "/Game/Props/SM_Door")
        self.assertEqual(len(request.render_spec_sha256), 64)

    def test_missing_asset_is_rejected_before_unreal_launches(self):
        with self.assertRaisesRegex(UnrealSceneBuildError, "missing asset"):
            resolve_scene_build_request(scene_spec(), [])

    def test_unsupported_asset_type_is_rejected_explicitly(self):
        with self.assertRaisesRegex(UnrealSceneBuildError, "supports static_mesh only"):
            resolve_scene_build_request(
                scene_spec(),
                [asset_card(asset_type="blueprint")],
            )

    def test_unsafe_asset_path_is_rejected(self):
        with self.assertRaisesRegex(UnrealSceneBuildError, "unsafe Unreal package path"):
            resolve_scene_build_request(
                scene_spec(),
                [asset_card(engine_path="/Game/Props/../Secret")],
            )

    def test_preflight_failure_blocks_unreal_launch(self):
        first = scene_spec().objects[0].model_dump(mode="json")
        first["object_id"] = "door_a"
        second = dict(first)
        second["object_id"] = "door_b"
        spec = scene_spec(objects=[first, second])

        with self.assertRaisesRegex(UnrealSceneBuildError, "preflight returned fail"):
            resolve_scene_build_request(spec, [asset_card()])

    def test_observed_actor_ids_and_kinds_must_match_request(self):
        request = resolve_scene_build_request(scene_spec(), [asset_card()])
        result = UnrealSceneBuildResult(
            status="succeeded",
            project_name="Example",
            world_name="Map",
            scene_name=request.scene_name,
            render_spec_sha256=request.render_spec_sha256,
            actors=[
                actor_record("door", "static_mesh", request.objects[0].transform),
                actor_record("camera", "camera", request.camera.transform),
                actor_record("key", "directional", request.lights[0].transform),
            ],
        )
        _validate_observed_result(request, result)

        wrong = result.model_copy(
            update={"actors": result.actors[:-1] + [actor_record("extra", "point")]}
        )
        with self.assertRaisesRegex(UnrealSceneBuildError, "does not match"):
            _validate_observed_result(request, wrong)

        moved = result.model_copy(deep=True)
        moved.actors[0].transform.location_cm.x += 1
        with self.assertRaisesRegex(UnrealSceneBuildError, "transforms do not match"):
            _validate_observed_result(request, moved)

    def test_result_loader_requires_failure_details(self):
        value = {
            "schema_version": "0.1",
            "status": "failed",
            "project_name": "Example",
            "world_name": "Map",
            "scene_name": "door_test",
            "render_spec_sha256": "a" * 64,
            "actors": [],
            "warnings": [],
            "errors": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(UnrealSceneBuildError, "must contain"):
                load_scene_build_result(path)

    def test_launcher_uses_offscreen_rhi_instead_of_null_rhi(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "Project.uproject"
            project.write_text("{}", encoding="utf-8")
            editor = root / "UE" / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
            editor.parent.mkdir(parents=True)
            editor.write_bytes(b"")
            spec_path = root / "scene.json"
            spec_path.write_text(scene_spec().model_dump_json(), encoding="utf-8")
            cards_path = root / "assets.json"
            cards_path.write_text(
                json.dumps([asset_card().model_dump(mode="json")]),
                encoding="utf-8",
            )
            output = root / "result.json"
            observed_command = []

            def fake_run(command, **kwargs):
                observed_command.extend(command)
                request = json.loads(
                    Path(kwargs["env"]["RENDERMASTER_SCENE_BUILD_REQUEST"]).read_text(
                        encoding="utf-8"
                    )
                )
                actors = [
                    {
                        "actor_id": item["object_id"],
                        "actor_kind": "static_mesh",
                        "actor_name": item["object_id"],
                        "class_name": "StaticMeshActor",
                        "transform": item["transform"],
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
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": "0.1",
                            "status": "succeeded",
                            "project_name": "Project",
                            "world_name": "Map",
                            "scene_name": request["scene_name"],
                            "render_spec_sha256": request["render_spec_sha256"],
                            "actors": actors,
                            "warnings": [],
                            "errors": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("render_master_bot.unreal_executor.subprocess.run", side_effect=fake_run):
                run_unreal_scene_build(
                    project,
                    engine_root=root / "UE",
                    render_spec_path=spec_path,
                    asset_catalog_path=cards_path,
                    output=output,
                )

            self.assertIn("-RenderOffscreen", observed_command)
            self.assertNotIn("-nullrhi", observed_command)


if __name__ == "__main__":
    unittest.main()

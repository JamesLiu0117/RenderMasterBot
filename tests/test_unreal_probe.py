import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.unreal_probe import UnrealProbeError, probe_unreal_project


CAPTURED_AT = datetime(2026, 8, 13, 18, 0, tzinfo=UTC)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_engine(root: Path, *, graph: bool = True) -> None:
    write_json(
        root / "Engine" / "Build" / "Build.version",
        {
            "MajorVersion": 5,
            "MinorVersion": 7,
            "PatchVersion": 4,
            "Changelist": 51494982,
        },
    )
    editor = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
    editor.parent.mkdir(parents=True, exist_ok=True)
    editor.write_bytes(b"")
    plugin_paths = [
        "Engine/Plugins/Experimental/PythonScriptPlugin/PythonScriptPlugin.uplugin",
        "Engine/Plugins/MovieScene/MovieRenderPipeline/MovieRenderPipeline.uplugin",
        (
            "Engine/Plugins/MovieScene/MoviePipelineMaskRenderPass/"
            "MoviePipelineMaskRenderPass.uplugin"
        ),
    ]
    for plugin_path in plugin_paths:
        write_json(root / plugin_path, {"FileVersion": 3, "EnabledByDefault": False})
    if graph:
        graph_dir = (
            root
            / "Engine"
            / "Plugins"
            / "MovieScene"
            / "MovieRenderPipeline"
            / "Source"
            / "MovieRenderPipelineEditor"
            / "Public"
            / "Graph"
        )
        graph_dir.mkdir(parents=True)


def make_project(root: Path, engine_root: Path, *, mounted: list[str]) -> Path:
    project_path = root / "RenderLab.uproject"
    write_json(
        project_path,
        {
            "FileVersion": 3,
            "EngineAssociation": "5.7",
            "Modules": [{"Name": "RenderLab", "Type": "Runtime"}],
            "Plugins": [{"Name": "StateTree", "Enabled": True}],
        },
    )
    log_lines = [
        f"LogInit: Base Directory: {engine_root.as_posix()}/Engine/Binaries/Win64/",
        "LogInit: Engine Version: 5.7.4-51494982+++UE5+Release-5.7",
    ]
    log_lines.extend(
        f"LogPluginManager: Mounting Engine plugin {plugin}" for plugin in mounted
    )
    log_path = root / "Saved" / "Logs" / "RenderLab.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    return project_path


class UnrealProjectProbeTests(unittest.TestCase):
    def test_mounted_python_and_movie_pipeline_are_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "UE_5.7"
            project_dir = root / "Project"
            make_engine(engine)
            project = make_project(
                project_dir,
                engine,
                mounted=[
                    "PythonScriptPlugin",
                    "MovieRenderPipeline",
                    "MoviePipelineMaskRenderPass",
                ],
            )

            manifest = probe_unreal_project(project, captured_at=CAPTURED_AT)

            self.assertEqual(manifest.engine_version, "5.7.4-51494982")
            self.assertTrue(manifest.python_available)
            self.assertTrue(manifest.movie_render_queue_available)
            self.assertTrue(manifest.movie_render_graph_available)
            self.assertEqual(manifest.supported_render_passes, ["beauty", "object_id"])
            self.assertEqual(manifest.project_modules, ["RenderLab"])

    def test_installed_but_unmounted_plugins_are_not_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "UE_5.7"
            project_dir = root / "Project"
            make_engine(engine)
            project = make_project(project_dir, engine, mounted=["StateTree"])

            manifest = probe_unreal_project(project, captured_at=CAPTURED_AT)

            self.assertFalse(manifest.python_available)
            self.assertFalse(manifest.movie_render_queue_available)
            self.assertFalse(manifest.movie_render_graph_available)
            self.assertEqual(manifest.supported_render_passes, [])
            status = {item.capability: item.status for item in manifest.evidence}
            self.assertEqual(status["movie_render_queue_available"], "not_detected")

    def test_uproject_can_explicitly_enable_an_installed_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "UE_5.7"
            project_dir = root / "Project"
            make_engine(engine)
            project = make_project(project_dir, engine, mounted=["StateTree"])
            descriptor = json.loads(project.read_text(encoding="utf-8"))
            descriptor["Plugins"].append({"Name": "MovieRenderPipeline", "Enabled": True})
            write_json(project, descriptor)

            manifest = probe_unreal_project(project, captured_at=CAPTURED_AT)

            self.assertTrue(manifest.movie_render_queue_available)
            evidence = next(
                item
                for item in manifest.evidence
                if item.capability == "movie_render_queue_available"
            )
            self.assertEqual(evidence.source, "uproject")

    def test_stale_solution_engine_path_becomes_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "UE_5.7"
            project_dir = root / "Project"
            make_engine(engine)
            project = make_project(project_dir, engine, mounted=["PythonScriptPlugin"])
            (project_dir / "RenderLab.sln").write_text(
                'Project = "C:\\OldEngine\\UE_5.7\\Engine\\Source\\UE5.csproj"',
                encoding="utf-8",
            )

            manifest = probe_unreal_project(project, captured_at=CAPTURED_AT)

            self.assertEqual(len(manifest.warnings), 1)
            self.assertIn("different engine root", manifest.warnings[0])

    def test_explicit_engine_root_works_without_a_project_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "UE_5.7"
            project_dir = root / "Project"
            make_engine(engine)
            project_dir.mkdir()
            project = project_dir / "RenderLab.uproject"
            write_json(project, {"FileVersion": 3, "EngineAssociation": "5.7"})

            manifest = probe_unreal_project(
                project,
                engine_root=engine,
                captured_at=CAPTURED_AT,
            )

            self.assertEqual(manifest.engine_version, "5.7.4-51494982")
            self.assertFalse(manifest.python_available)

    def test_invalid_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-a-project.txt"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(UnrealProbeError, "not an existing .uproject"):
                probe_unreal_project(path)


if __name__ == "__main__":
    unittest.main()

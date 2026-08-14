"""Static Unreal project capability discovery with auditable evidence."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from render_master_bot.contracts import CapabilityEvidence, CapabilityManifest
from render_master_bot.serialization import canonical_sha256


class UnrealProbeError(RuntimeError):
    """Raised when a project cannot produce a trustworthy manifest."""


_BASE_DIRECTORY_RE = re.compile(
    r"LogInit: Base Directory:\s*(.+?)/Engine/Binaries/(?:Win64|Linux|Mac)/?\s*$",
    re.MULTILINE,
)
_ENGINE_VERSION_RE = re.compile(r"LogInit: Engine Version:\s*([^\r\n]+)")
_MOUNTED_PLUGIN_RE = re.compile(
    r"LogPluginManager: Mounting (?:Engine|Project) plugin ([^\r\n]+)"
)
_SOLUTION_ENGINE_RE = re.compile(r'([A-Za-z]:\\[^"\r\n]*?\\UE_[^\\"\r\n]+)\\Engine\\')

_RELEVANT_PLUGIN_PATHS = {
    "PythonScriptPlugin": Path(
        "Engine/Plugins/Experimental/PythonScriptPlugin/PythonScriptPlugin.uplugin"
    ),
    "MovieRenderPipeline": Path(
        "Engine/Plugins/MovieScene/MovieRenderPipeline/MovieRenderPipeline.uplugin"
    ),
    "MoviePipelineMaskRenderPass": Path(
        "Engine/Plugins/MovieScene/MoviePipelineMaskRenderPass/"
        "MoviePipelineMaskRenderPass.uplugin"
    ),
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UnrealProbeError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise UnrealProbeError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UnrealProbeError(f"expected a JSON object in {path}")
    return value


def _latest_log(project_dir: Path) -> Path | None:
    log_dir = project_dir / "Saved" / "Logs"
    if not log_dir.is_dir():
        return None
    logs = sorted(
        (path for path in log_dir.glob("*.log") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return logs[0] if logs else None


def _log_facts(path: Path | None) -> tuple[Path | None, str | None, set[str]]:
    if path is None:
        return None, None, set()
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None, None, set()

    base_match = _BASE_DIRECTORY_RE.search(text)
    engine_root = Path(base_match.group(1).replace("/", "\\")) if base_match else None
    version_match = _ENGINE_VERSION_RE.search(text)
    engine_version = version_match.group(1).strip() if version_match else None
    mounted_plugins = {
        match.group(1).strip() for match in _MOUNTED_PLUGIN_RE.finditer(text)
    }
    return engine_root, engine_version, mounted_plugins


def _solution_engine_roots(project_dir: Path) -> set[Path]:
    roots: set[Path] = set()
    for solution in project_dir.glob("*.sln"):
        try:
            text = solution.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        roots.update(Path(value) for value in _SOLUTION_ENGINE_RE.findall(text))
    return roots


def _valid_engine_root(path: Path | None) -> bool:
    if path is None:
        return False
    return (
        (path / "Engine" / "Build" / "Build.version").is_file()
        and (path / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe").is_file()
    )


def _resolve_engine_root(
    project_dir: Path,
    association: str | None,
    log_root: Path | None,
    engine_root: Path | None,
) -> tuple[Path, str, list[str]]:
    warnings: list[str] = []
    if engine_root is not None:
        resolved = engine_root.expanduser().resolve()
        if not _valid_engine_root(resolved):
            raise UnrealProbeError(
                f"--engine-root does not contain Build.version and UnrealEditor.exe: {resolved}"
            )
        return resolved, "user_override", warnings

    if _valid_engine_root(log_root):
        selected = log_root.resolve()
        source = "project_log"
    else:
        candidates = sorted(
            (
                path.resolve()
                for path in _solution_engine_roots(project_dir)
                if _valid_engine_root(path)
            ),
            key=str,
        )
        if association:
            common = [
                Path(f"C:/Program Files/Epic Games/UE_{association}"),
                Path(f"E:/Unreal Engine/UE_{association}"),
                Path(f"D:/Epic Games/UE_{association}"),
            ]
            candidates.extend(path.resolve() for path in common if _valid_engine_root(path))
        unique = list(dict.fromkeys(candidates))
        if not unique:
            raise UnrealProbeError(
                "could not locate the associated Unreal Engine; pass --engine-root explicitly"
            )
        selected = unique[0]
        source = "solution" if selected in _solution_engine_roots(project_dir) else "filesystem"

    for solution_root in sorted(_solution_engine_roots(project_dir), key=str):
        if solution_root.resolve() != selected:
            warnings.append(
                "The generated Visual Studio solution references a different engine root "
                f"({solution_root}) than the selected runtime engine ({selected}). "
                "Regenerate project files if builds use the stale path."
            )
    return selected, source, warnings


def _engine_version(engine_root: Path) -> str:
    build = _read_json(engine_root / "Engine" / "Build" / "Build.version")
    try:
        version = f"{build['MajorVersion']}.{build['MinorVersion']}.{build['PatchVersion']}"
        changelist = int(build["Changelist"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnrealProbeError("Build.version is missing required version fields") from exc
    return f"{version}-{changelist}"


def _explicit_plugins(project: dict[str, Any]) -> set[str]:
    enabled: set[str] = set()
    for value in project.get("Plugins", []):
        if isinstance(value, dict) and value.get("Enabled") is True and value.get("Name"):
            enabled.add(str(value["Name"]))
    return enabled


def _project_modules(project: dict[str, Any]) -> list[str]:
    names = {
        str(value["Name"])
        for value in project.get("Modules", [])
        if isinstance(value, dict) and value.get("Name")
    }
    return sorted(names, key=str.casefold)


def _plugin_installed(engine_root: Path, name: str) -> bool:
    relative = _RELEVANT_PLUGIN_PATHS[name]
    return (engine_root / relative).is_file()


def _evidence(
    capability: str,
    status: str,
    source: str,
    detail: str,
) -> CapabilityEvidence:
    return CapabilityEvidence(
        capability=capability,
        status=status,
        source=source,
        detail=detail,
    )


def probe_unreal_project(
    uproject_path: str | Path,
    *,
    engine_root: str | Path | None = None,
    captured_at: datetime | None = None,
) -> CapabilityManifest:
    """Create a conservative static CapabilityManifest for one Unreal project."""

    project_path = Path(uproject_path).expanduser().resolve()
    if project_path.suffix.casefold() != ".uproject" or not project_path.is_file():
        raise UnrealProbeError(f"not an existing .uproject file: {project_path}")

    project = _read_json(project_path)
    project_dir = project_path.parent
    association_value = project.get("EngineAssociation")
    association = str(association_value) if association_value else None
    log_path = _latest_log(project_dir)
    log_root, log_version, mounted_plugins = _log_facts(log_path)
    resolved_root, root_source, warnings = _resolve_engine_root(
        project_dir,
        association,
        log_root,
        Path(engine_root) if engine_root is not None else None,
    )
    version = _engine_version(resolved_root)

    explicit_plugins = _explicit_plugins(project)
    enabled_plugins = mounted_plugins | explicit_plugins

    evidence: list[CapabilityEvidence] = [
        _evidence(
            "engine_version",
            "confirmed",
            "engine_build",
            f"Build.version reports Unreal Engine {version}.",
        ),
        _evidence(
            "engine_root",
            "confirmed",
            root_source,
            "The selected engine root contains Build.version and UnrealEditor.exe.",
        ),
    ]
    if log_version and not log_version.startswith(version):
        warnings.append(
            f"The latest project log reports {log_version}, but Build.version reports {version}."
        )
        evidence.append(
            _evidence(
                "engine_version",
                "conflict",
                "project_log",
                f"The latest project log reports {log_version}.",
            )
        )
    elif log_version:
        evidence.append(
            _evidence(
                "engine_version",
                "confirmed",
                "project_log",
                f"The latest project log reports {log_version}.",
            )
        )

    def enabled_source(plugin_name: str) -> str:
        return "project_log" if plugin_name in mounted_plugins else "uproject"

    python_installed = _plugin_installed(resolved_root, "PythonScriptPlugin")
    python_enabled = "PythonScriptPlugin" in enabled_plugins
    python_available = python_installed and python_enabled
    evidence.append(
        _evidence(
            "python_available",
            "confirmed" if python_available else "not_detected",
            enabled_source("PythonScriptPlugin") if python_enabled else "plugin_descriptor",
            (
                "PythonScriptPlugin is installed and mounted in the latest project log."
                if python_available
                else "PythonScriptPlugin is not both installed and enabled for this project."
            ),
        )
    )

    mrq_installed = _plugin_installed(resolved_root, "MovieRenderPipeline")
    mrq_enabled = "MovieRenderPipeline" in enabled_plugins
    mrq_available = mrq_installed and mrq_enabled
    evidence.append(
        _evidence(
            "movie_render_queue_available",
            "confirmed" if mrq_available else "not_detected",
            enabled_source("MovieRenderPipeline") if mrq_enabled else "plugin_descriptor",
            (
                "MovieRenderPipeline is installed and enabled."
                if mrq_available
                else "MovieRenderPipeline is installed but was not enabled or mounted."
                if mrq_installed
                else "MovieRenderPipeline was not found in the selected engine installation."
            ),
        )
    )

    graph_code_present = mrq_installed and (
        resolved_root
        / "Engine"
        / "Plugins"
        / "MovieScene"
        / "MovieRenderPipeline"
        / "Source"
        / "MovieRenderPipelineEditor"
        / "Public"
        / "Graph"
    ).is_dir()
    mrg_available = mrq_available and graph_code_present
    evidence.append(
        _evidence(
            "movie_render_graph_available",
            "confirmed" if mrg_available else "not_detected",
            "filesystem" if graph_code_present else "plugin_descriptor",
            (
                "Movie Render Graph code is present and MovieRenderPipeline is enabled."
                if mrg_available
                else "Movie Render Graph code is installed, but MovieRenderPipeline is disabled."
                if graph_code_present
                else "Movie Render Graph code was not detected."
            ),
        )
    )

    supported_render_passes = ["beauty"] if mrq_available else []
    mask_installed = _plugin_installed(resolved_root, "MoviePipelineMaskRenderPass")
    if mask_installed and "MoviePipelineMaskRenderPass" in enabled_plugins:
        supported_render_passes.append("object_id")

    enabled_plugin_list = sorted(enabled_plugins, key=str.casefold)
    if len(enabled_plugin_list) > 256:
        warnings.append(
            f"The probe detected {len(enabled_plugin_list)} enabled plugins; "
            "CapabilityManifest stores the first 256 in case-insensitive sorted order."
        )
        enabled_plugin_list = enabled_plugin_list[:256]

    try:
        return CapabilityManifest(
            engine="unreal",
            engine_version=version,
            engine_association=association,
            project_name=project_path.stem,
            project_descriptor_sha256=canonical_sha256(project),
            coordinate_system="unreal_z_up_cm",
            probe_mode="static",
            python_available=python_available,
            movie_render_queue_available=mrq_available,
            movie_render_graph_available=mrg_available,
            project_modules=_project_modules(project),
            enabled_plugins=enabled_plugin_list,
            supported_asset_types=[
                "Blueprint",
                "LevelSequence",
                "Material",
                "SkeletalMesh",
                "StaticMesh",
                "Texture2D",
                "World",
            ],
            supported_render_passes=supported_render_passes,
            evidence=evidence,
            warnings=warnings,
            captured_at=captured_at or datetime.now(UTC),
        )
    except ValidationError as exc:
        raise UnrealProbeError(f"probe produced an invalid CapabilityManifest: {exc}") from exc

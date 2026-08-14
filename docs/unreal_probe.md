# Unreal Project Probe

The project probe creates a conservative `CapabilityManifest` before the AI
planner or deterministic adapter assumes that an Unreal feature is usable.
The current implementation is read-only and static: it does not launch or
modify Unreal Editor.

## Evidence priority

Engine discovery uses the following precedence:

1. explicit `--engine-root` supplied by the operator;
2. the base directory in the most recent Unreal project log;
3. valid engine paths found in generated Visual Studio solutions;
4. conservative association-based installation candidates.

The selected root must contain both `Engine/Build/Build.version` and the Win64
Unreal Editor executable. `Build.version` is the authoritative source for the
exact engine version and changelist.

## Installed is not the same as enabled

Relevant plugin descriptors prove that a feature is installed. The latest
project log or an explicit project declaration proves that it is enabled. A
capability is reported as available only when both conditions hold.

For example, an installed but disabled `MovieRenderPipeline` produces:

```json
{
  "capability": "movie_render_queue_available",
  "status": "not_detected",
  "source": "plugin_descriptor",
  "detail": "MovieRenderPipeline is installed but was not enabled or mounted."
}
```

This prevents the planner from creating MRQ/MRG operations that the current
project cannot execute.

## Usage

```powershell
render-master unreal-probe `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --output capability_manifest.json
```

Override engine discovery when the project has no useful recent log:

```powershell
render-master unreal-probe MyProject.uproject `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --output capability_manifest.json
```

The command exits with code 1 when the `.uproject` is invalid or a trustworthy
engine installation cannot be located. Missing optional features such as MRQ
are recorded in evidence and do not make the probe itself fail.

## Static probe limits

The static probe confirms files, descriptors, declarations, and the latest
recorded plugin mounts. It does not yet prove that a live Editor session can:

- import the `unreal` Python module and execute an Editor command;
- enumerate assets through the Asset Registry;
- create a Movie Render Queue or Graph job;
- render a specific pass on the active RHI and project configuration.

Those checks will be added as an `editor_runtime` probe after the integration
bootstrap is installed in the Unreal project.

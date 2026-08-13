# Shared contracts: version 0.1

The AI core and engine adapters communicate through versioned JSON contracts.
Model output never calls Unreal directly; it must first validate against one of
these strict models. Unknown fields are rejected.

| Contract | Producer | Consumer | Purpose |
| --- | --- | --- | --- |
| `TechniqueCard` | knowledge ingestion | retriever/planner | cited graphics knowledge |
| `AssetCard` | Unreal asset scanner | retriever/planner/adapter | resolvable project assets |
| `RenderSpec` | planner or human | validator/engine adapter | complete requested scene |
| `RenderSpecPatch` | evaluator or human | patch validator | bounded correction proposal |
| `EvaluationReport` | visual/rule evaluator | repair planner/benchmark | findings and verdict |
| `CapabilityManifest` | Unreal project probe | planner/adapter | observed engine capabilities |
| `RunManifest` | orchestration layer | dataset builder/auditor | reproducible run evidence |

Although the early planning note called these "six contracts," the actual
boundary contains seven top-level contracts once `CapabilityManifest` is
counted separately. Keeping it separate prevents the planner from assuming
plugins or render passes that a specific Unreal project does not have.

## Safety properties

- Every model uses `extra="forbid"`; invented keys fail validation.
- `RenderSpecPatch` can modify only scene content (`objects`, `camera`,
  `lights`, `render`, and `notes`). It cannot rewrite contract metadata.
- A `pass` evaluation cannot contain an error or blocking issue.
- Preflight evaluation can run before an image exists; preview and final
  evaluation must point to at least one rendered image.
- Suggested patches must target the exact SHA-256 identity of the evaluated
  `RenderSpec`.
- Artifact paths are run-relative and portable; absolute and parent paths are
  rejected.
- Terminal runs require an end timestamp, and failed runs require an error.

## CLI

```powershell
render-master schema technique-card
render-master schema capability-manifest -o capability.schema.json
render-master validate examples/asset_card.json --contract asset-card
render-master validate examples/run_manifest.json --contract run-manifest
```

`render-master schema` and `render-master validate` default to `render-spec`,
so the version 0.1 command line remains backward compatible.

# Training Stage 2: Unreal-validated Golden References

## Outcome

Stage 2 creates trusted planner targets without treating a base-model failure as
its own correction. A Golden Reference is authored by a human or a deterministic
compiler, bound to the frozen Stage 1 suite and AssetCard catalog, and rendered
unchanged in Unreal before it may enter the existing campaign-gated dataset.

```text
human or deterministic RenderSpec
    -> frozen TrainingReferenceSuite
    -> prompt + asset + type + slot + expectation checks
    -> renderer-independent preflight pass
    -> planner-free Unreal preview
    -> PNG integrity + visual evaluation
    -> ordinary TrainingCampaignRun eligibility
    -> existing grouped dataset build and leakage audit
    -> QLoRA only when the audited 20/2/2 gate passes
```

The current model is not allowed to generate a reference and then certify it as
ground truth. `authoring_source` accepts only `human_authored` or
`deterministic_compiler`. Explanatory rationale is stored outside `RenderSpec`;
reference targets require an empty `notes` list so commentary cannot become an
executable training label.

## Static trust boundary

`training-reference validate` performs no model or Unreal work. It rejects:

- a stale `.uproject`, AssetCard catalog, campaign suite, or reference hash;
- references for unknown campaign cases;
- a `source_prompt` that differs from the frozen request;
- used assets absent from the reference retrieval context;
- unknown assets, non-mesh scene objects, or non-material assignments;
- dimensionless scene meshes or invalid material-slot names;
- missing required assets, forbidden assets, or wrong object/material counts;
- any RenderSpec whose deterministic preflight verdict is not `pass`.

A partial reference suite can pass and run as a pilot. `full_coverage` remains
false and `missing_case_ids` records the remaining authoring work.

## Compile deterministic single-asset references

The first compiler covers frozen `single_asset` cases only. It selects the exact
required StaticMesh, assigns no unsolicited material, preserves the Stage 1
retrieval candidates, and produces a bounded elevated three-quarter camera with
fixed exposure and neutral key/fill lighting. Output is immutable: the command
refuses to overwrite an existing reference suite.

```powershell
$Baseline = "$PrivateRoot\data\training\campaigns\optimization-baseline-v1"
$References = "$PrivateRoot\data\training\references\optimization-golden-single-assets-v2.json"

render-master training-reference compile-single-assets `
  --suite "$Repo\examples\training_campaign_suite_optimization_v1.json" `
  --path "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --assets $Catalog `
  --reference-suite-id optimization_single_asset_references_v2 `
  --retrieval-campaign $Baseline `
  --output $References
```

Use repeatable `--case-id` arguments to compile a smaller pilot without changing
the source suite. The compiler output must still pass the static validator and
the real Unreal gates below before it is training-eligible.

```powershell
$PrivateRoot = "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project"
$Repo = "E:\RenderBot\RenderMasterBot"
$Catalog = "$PrivateRoot\data\assets\optimization-plugin\full-scan-003\asset_cards.json"
$References = "$PrivateRoot\data\training\references\optimization-golden-v1.json"

render-master training-reference validate `
  --references $References `
  --suite "$Repo\examples\training_campaign_suite_optimization_v1.json" `
  --path "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --assets $Catalog `
  --output "$PrivateRoot\data\training\references\validation_report.json"
```

## Freeze a planner-free campaign

Planning validates every selected reference, creates the normal campaign
manifest and suite snapshot, and adds a hash-bound
`reference_suite_snapshot.json`. It does not start Ollama or Unreal.

```powershell
$Campaign = "$PrivateRoot\data\training\campaigns\optimization-golden-v1"

render-master training-reference plan `
  --references $References `
  --suite "$Repo\examples\training_campaign_suite_optimization_v1.json" `
  --campaign-dir $Campaign `
  --campaign-id optimization_golden_v1 `
  --path "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --assets $Catalog `
  --vision-model qwen3-vl:8b-instruct `
  --output "$Campaign\plan_summary.json"
```

The campaign planner identity is `golden-reference-v0.1` with provider `human`
or `local`; it is provenance, not a model invocation. Mixed or deterministic
reference suites use `local`.

## Render and gate references

The runner writes the exact reference as `planner_raw`, `initial_render_spec`,
and the iteration RenderSpec for compatibility with the production training
message builder. No scene planner, correction model, camera framing, studio
calibration, or model-authored patch may change it.

```powershell
render-master training-reference run `
  --references $References `
  --suite "$Repo\examples\training_campaign_suite_optimization_v1.json" `
  --campaign-dir $Campaign `
  --path "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --assets $Catalog `
  --output "$Campaign\run_summary.json"
```

Each case receives one actual Unreal preview, deterministic image statistics,
and one visual evaluation. `reference_pixel_quality.json` independently rejects
blank, underexposed, or overexposed previews; more than 3% clipped pixels; less
than 8% foreground; or center luminance outside 5%-75%. The model cannot
override this host gate. Both gates must pass before `final_render_spec` is
published and the case can become campaign-eligible. A non-pass keeps all
evidence but is excluded from positive SFT data. Transient failures can be
retried into a new attempt directory; prior evidence is never overwritten.
Headless previews use Unreal's `-DDC-ForceMemoryCache` so restricted automation
does not require a writable global Derived Data Cache or a broad Windows
security exception. `-ShaderWorkingDir` is also scoped to the run's temporary
directory so ShaderCompileWorker never depends on a writable user-profile path.

## Dataset compatibility and readiness

Use the existing Stage 1 commands after the reference campaign completes:

```powershell
$Dataset = "$PrivateRoot\data\training\datasets\optimization-golden-v1"

render-master training-data build `
  --source-root $Campaign `
  --output-dir $Dataset `
  --dataset-id optimization_golden_v1 `
  --campaign-run "$Campaign\campaign_manifest.json" `
  --output "$Dataset\build_summary.json"

render-master training-data audit "$Dataset\dataset_manifest.json"
```

Golden-derived training examples include the hash-pinned
`golden_reference_case` in `source_artifacts`. The campaign hash also binds the
complete reference-suite identity. The existing grouped split, duplicate,
membership, hash, and cross-split leakage checks remain authoritative.

The minimum QLoRA smoke gate remains 20 train, 2 validation, and 2 test cases.
The practical first target is 32-40 diverse references so validation and test
coverage are not limited to one or two intent families.

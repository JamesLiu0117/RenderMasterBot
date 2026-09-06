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

Material-assignment cases use a separate deterministic compiler. It accepts only
cases with exactly one required mesh, one required material, one object, and one
material assignment. The mesh must expose exactly one catalog slot so the host
can bind it without model judgment.

```powershell
$MaterialReferences = "$PrivateRoot\data\training\references\optimization-golden-materials-v1.json"

render-master training-reference compile-materials `
  --suite "$Repo\examples\training_campaign_suite_optimization_v1.json" `
  --path "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --assets $Catalog `
  --reference-suite-id optimization_material_references_v1 `
  --retrieval-campaign $Baseline `
  --output $MaterialReferences
```

The compiler assigns the required material to the sole catalog slot, reuses the
same bounded product-preview camera and neutral light rig, and preserves both
required asset IDs in the frozen retrieval context. Multi-slot meshes are left
for human-authored references until the campaign contract names an exact slot.

`training-reference compile-lighting` uses the same CLI arguments and supports
the four named lighting cases in the frozen OptimizationPlugin suite. Its
reviewable recipes hold exposure at EV100 11 and preserve the single-object
camera, while varying key/fill illuminance and adding a rear directional rim:

| Case | Key lux | Fill lux | Rim lux |
| --- | ---: | ---: | ---: |
| `lighting_door_high_key` | 18,000 | 12,000 | 0 |
| `lighting_pistol_dramatic` | 18,000 | 1,500 | 8,000 |
| `lighting_rifle_rim` | 16,000 | 5,000 | 18,000 |
| `lighting_target_contrast` | 20,000 | 2,500 | 6,000 |

Unknown lighting case IDs and non-single-object expectations are rejected.
These recipes are proposed reference labels, not a general lighting designer:
the actual preview must still satisfy the prompt evaluator and pixel gate.

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

## Merge completed campaigns into one dataset

Builder `0.3` rebuilds examples from explicitly selected campaign evidence. It
does not concatenate independently partitioned JSONL files. All campaigns must
be completed and share the same project descriptor and AssetCard catalog hashes.
Only their latest eligible attempts can enter the dataset. Unselected campaign
directories are not scanned. Input order does not affect the generated bytes.

```powershell
$CampaignRoot = "$PrivateRoot\data\training\campaigns"
$Merged = "$PrivateRoot\data\training\datasets\optimization-golden-merged-v1"

render-master training-data merge `
  --source-root $CampaignRoot `
  --output-dir $Merged `
  --dataset-id optimization_golden_merged_v1 `
  --campaign-run "$CampaignRoot\optimization-golden-single-assets-v2\campaign_manifest.json" `
  --campaign-run "$CampaignRoot\optimization-golden-materials-v1\campaign_manifest.json" `
  --output "$Merged\build_summary.json"

render-master training-data audit "$Merged\dataset_manifest.json" `
  --source-root $CampaignRoot `
  --output "$Merged\source_audit.json"
```

Every source campaign is copied byte-for-byte into `provenance/campaigns/`.
The dataset manifest binds its original relative path, snapshot path, and
SHA-256. The ordinary audit rechecks snapshots and confirms that example source
hashes and groups agree with campaign eligibility. With `--source-root`, it
also rehashes original campaign/workflow evidence, verifies workflow input and
output artifacts, and reproduces each example. A plain portable audit cannot
attest that original evidence still exists or is unchanged. `merge` performs
the source audit automatically before reporting its result. Audit reports state
`source_evidence_checked` and `campaign_snapshot_count` explicitly.

Exact duplicate message content is excluded before splitting. Builder `0.3`
derives each `source_group_id` from the project hash, catalog hash, and case ID,
so repeated versions of one case stay in one partition even across campaigns.
This guards case reruns; it does not establish semantic independence of different
case IDs or perform an asset-disjoint evaluation. Earlier builder `0.1`/`0.2`
datasets remain readable and keep their workflow-based grouping.

Merged datasets are new immutable versions: adding groups can change the ranked
train/validation/test assignment. Freeze the final dataset manifest before a
training experiment and never compare models across different holdout versions.
The 12-example single-asset/material merge was source-audited on 2026-09-05 with
10 train, 1 validation, and 1 test example, zero exclusions and zero audit errors.
It remains below the 20/2/2 QLoRA smoke gate.

The four lighting references then completed on 2026-09-05 with 4/4 workflow,
expectation, pixel, and visual passes, one attempt each. Clipped-pixel fractions
were 0.00000, 0.00179, 0.00229, and 0.00053 for high-key, low-key, rim, and
contrast respectively. All four saved previews were also visually inspected.
Adding `optimization-golden-lighting-v1/campaign_manifest.json` as a third
`--campaign-run` produced `optimization-golden-merged-v2`: 16 accepted examples,
12 train / 2 validation / 2 test, no exclusions, and zero source-audit errors.
Its dataset SHA-256 is
`4aa7af3d859e53c8e9a79d9252bc8f8b04bd459ab0da0c98cab672e6bce01c6c`.
Eight additional training examples are still needed for the smoke gate; no
model-weight training has started. The remaining frozen intent families are
four camera-view and four multi-asset-composition cases, each requiring its own
reference authoring and real-preview validation.

Asset qualification remains separate from these structural gates. In this
test project, `/Game/LevelPrototyping/Interactable/Door/Meshes/SM_Door` renders
as a cube-like 200 x 200 x 200 cm block at unit scale. The Asset Registry and
real Unreal bounds agree, and the spawn log confirms the requested asset was
loaded. Its cases exercise exact catalog selection, materials, and lighting;
they are not evidence of realistic architectural-door geometry coverage.

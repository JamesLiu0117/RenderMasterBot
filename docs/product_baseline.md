# Reproducible product baseline

RenderMasterBot uses a product baseline to identify the exact source, runtime,
models, Unreal installation, project, assets, training data, benchmark, and
capability claims associated with an evaluation. This prevents a component test,
fixture interpretation, or Golden Reference run from being reported as an
end-to-end assistant result.

## Evidence levels

The capability ledger uses these ordered evidence levels:

1. `none`: no implementation evidence.
2. `unit_or_fixture`: deterministic tests or fixture-only execution.
3. `live_model_fixture`: a configured local model interpreted supplied fixture data.
4. `live_engine_component`: a bounded component ran against the real Unreal project.
5. `complete_user_task`: a user request completed through conversation, required tools,
   approval, execution, verification, and a grounded follow-up.

Implementation status and evidence level are deliberately independent. A tool can be
implemented while its highest accepted evidence remains a fixture test.

## Frozen product task suite

`examples/product_benchmark_suite_v1.json` is the versioned development-regression
suite. It includes bilingual capability questions, project inspection, missing-context
clarification, scene previews, bounded edits, profiling, graphics knowledge, and an
unsupported request. Every case declares an evaluation stage:

- `router_only`: intent, outcome, and required context can be checked immediately.
- `tool_result`: the assistant must consume and explain typed tool evidence.
- `approval_loop`: the proposal, user decision, stale-state checks, and apply result matter.
- `complete_user_task`: the request must reach a verified result and grounded follow-up.

The embedded `suite_sha256` is the SHA-256 of the canonical JSON payload excluding that
field. Editing a case without intentionally refreezing the identity fails validation.
The suite is a development regression set because its contents are visible during
implementation. Product claims require separate unseen holdouts.

Validate it with:

```powershell
render-master product-benchmark-validate `
  E:\RenderBot\RenderMasterBot\examples\product_benchmark_suite_v1.json
```

## Private capability ledger

The evidence ledger belongs outside Git because it contains machine paths and records
the current local state. Each capability states implementation status, highest accepted
evidence, evidence paths, and remaining acceptance gaps. Validate it with:

```powershell
render-master capability-evidence-validate `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\baselines\capability-evidence-v1.json"
```

## Runtime identity capture

Run the capture only after source and configuration changes for the evaluated baseline
are complete. The command records both the Git commit and a SHA-256 over current tracked
and non-ignored source-file contents, so a dirty working tree remains identifiable.
Ollama model names alone are insufficient: the installed digest and byte size are saved.

```powershell
render-master product-baseline-capture `
  E:\OptimizationPlugin\OptimizationPlugin.uproject `
  --baseline-id optimization_product_baseline_2026_09_05_v1 `
  --source-root E:\RenderBot\RenderMasterBot `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\full-scan-003\asset_cards.json" `
  --dataset "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\training\datasets\optimization-golden-merged-v2\dataset_manifest.json" `
  --benchmark-suite E:\RenderBot\RenderMasterBot\examples\product_benchmark_suite_v1.json `
  --evidence-ledger "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\baselines\capability-evidence-v1.json" `
  --output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\baselines\runtime-baseline-2026-09-05.json"
```

The runtime baseline is configuration evidence, not a benchmark score. Evaluation
reports should reference its `baseline_sha256` and preserve historical results instead
of overwriting them.

## Safe campaign pause and resume

Stopping a parent PowerShell process does not always deliver `KeyboardInterrupt` to the
Python runner. If the process has been stopped but the manifest still reports an active
case, recover all terminal evidence and persist a resumable cancellation explicitly:

```powershell
render-master training-campaign cancel `
  "C:\path\to\campaign\campaign_manifest.json" `
  --reason "Paused at operator request" `
  --output "C:\path\to\campaign\pause_summary.json"
```

The command is idempotent. It never starts a model or Unreal process. If the active
workflow already wrote a valid terminal manifest, its actual result is preserved; a
nonterminal manifest is recorded as interrupted. A later `training-campaign run`
without `--retry-failed` resumes at the next uncompleted case and retains all attempt
directories.

## Phase 1 conversational result-loop status

`AssistantToolResultEvent` is the host-authored boundary between a registered
capability and the next conversation turn. It distinguishes proposal, analysis,
execution, verification, rejection, failure, and cancellation phases; restricts
their legal statuses; preserves proposal/report and subject identities; and can
carry SHA-256-bound proposal, report, capture, render, trace, or log evidence.

Transform is the first vertical integration. A conversationally routed Transform
records the originating turn, proposal identity, selected Actor paths, and proposal
file hash when planning completes. Approval and rejection produce separate events,
so a proposal can never be interpreted as an applied edit. The Unreal request writer
and Python router both validate these records, and the Editor automation suite covers
serialization plus cross-conversation and phase/status rejection.

The bounded current session is atomically persisted outside Git under the configured
workflow root. It restores messages and results across panel/Editor restart. A vanished
planning process is recorded as cancelled. A pending Transform proposal is reviewable
again only after its proposal-file SHA-256 and all live Actor Before evidence are
revalidated; stale or tampered state removes execution authority and records failure.

The plugin uses its own tested portable SHA-256 evidence utility because UE 5.7's
generic platform SHA-256 entry point is an asserting stub in the validated Windows
Editor environment. Known-vector automation protects the replacement, and proposal,
lighting-review, Insights trace, and shadow-verification callers share it.

This still does not complete Phase 1. Only Transform has a result adapter, and a
complete profiling or visual-verification result is not yet available to follow-up
conversation. A common adapter for the remaining specialists is the next slice.

# Unreal Editor panel

`unreal/RenderMasterBot` is the native Slate workspace for the RenderMasterBot
graphics assistant. It is an Editor-only plugin: it does not ship model
weights, start a network service, or add runtime code to a packaged game.

## Workspace model

The default **Assistant** page is the conversational product entry point. It
keeps a bounded local user/assistant message timeline and reads the live project name,
current level, PIE/SIE mode, selected actors, and relevant completed-workflow
state directly from the Editor. **Send to Assistant** produces exactly one
strict result: a read-only answer, one clarifying question, or a route to one
registered bounded capability. The operator then reviews any action scope and
explicitly approves or rejects it. Context inspection, conversation routing,
and proposal generation do not modify the Editor scene.

Capability-specific entry buttons are collapsed under **Advanced Direct
Tools**. They remain useful for debugging and expert operation but are not the
normal interaction model. The current session is atomically persisted as
`assistant-conversations/editor_session.json` under the configured workflow
root, outside the source repository. **New Conversation** replaces that current
session with a fresh local conversation ID. It does not delete immutable turn,
proposal, capture, trace, or run artifacts and does not modify the Unreal project.

Conversation remains available while a stable Action Card waits for approval,
so the operator can ask a follow-up question. The router will not start a
second action until the current card is approved or rejected. Transform is the
first capability connected to the typed result loop: proposal completion,
failure, apply, and rejection are appended to the conversation with the source
turn, exact proposal ID, Actor paths, and SHA-256 identity of the proposal file.
The next router turn receives at most 12 recent host-authored result events and
therefore does not have to infer execution state from conversational prose.
Messages and result events survive panel/Editor restart. A planning process is
not silently restarted; it becomes a `cancelled` result. A pending Transform
card is restored only when the proposal-file SHA-256 and all live Actor identity,
root-component, mobility, lock, and Before Transform evidence still match.
Otherwise Apply remains unavailable and the session records a failure. Other
specialist result adapters remain Phase 1 work.
Active planning, capture, import, render, and profiling processes still
serialize model turns.

The connected direct Editor actions are material selection, one-to-32-Actor
world/local Transform editing, compatible one-to-16-Light group property
editing, camera-relative three-point-light coordination, and one-Camera
property editing.

**Run Full Level Audit** additionally inspects every eligible native
StaticMeshActor in the current Editor level without requiring a selection. It
computes deterministic static priorities, sends only the Top 64 to the local
model, and may return approval-gated Cast Shadow or Max Draw Distance actions.

**Run Profiling-First Level Audit** requires an active fixed PIE/SIE workload and
automatically binds that static funnel to runtime samples, a GPU trace, and up
to two repeatable Actor studies. It is read-only and uses profiler evidence,
not rendered images, to describe performance.

Material selection supports one selected Actor that contains exactly one valid
Static Mesh Component. A single
slot is targeted automatically. For a multi-slot mesh, the operator chooses an
explicit target in the **Target material slot** menu, which shows every slot and
its current material path. **Prepare Material** searches only project materials
present in the validated catalog. **Search Poly Haven** searches official CC0
materials, caches and hash-verifies four PBR maps outside the project, and shows
the source, license, approval hash, and exact five Content paths before mutation.

**Approve & Apply Material** handles a project material. **Approve Import &
Apply** handles the exact external proposal: it creates and saves four textures
and one material, synchronizes the AssetCard catalog and Chroma, and applies the
new material. Both first check that the Actor, component, mesh, slot, and current
material have not changed. The level is not saved automatically and Ctrl+Z
restores the previous override. External Content assets remain saved. **Reject**
makes no scene or Content change, although verified downloads remain cached.

**Prepare Transform** accepts one to 32 selected Actors and one world- or
local-space translation, rotation, or scale request. It does not depend on the
Actors having Static Meshes. The resulting **Transform Action** card shows the
coordinate space, complete frozen selection, and exact per-Actor world-space
Before/After values. **Approve & Apply Transform** rechecks every Actor; if any
identity, root component, mobility, lock state, or Transform is stale, nothing
is applied. A successful batch uses one `FScopedTransaction`, never saves the
level automatically, and is restored by one Ctrl+Z.
For a Transform started through **Send to Assistant**, the timeline separately
records `needs_review`, `applied`, `rejected`, or `failed`; generating a card is
never reported as applying the edit.

**Prepare Light** accepts one to 16 selected Directional, Point, Spot, or Rect
Lights and applies one compatible intent to the complete ordered selection. Its
**Light Action** card shows every frozen light type and intensity unit plus the
complete per-light Before/After state. Intensity percentage changes can span
mixed non-EV units while preserving each light's unit; absolute intensity
changes require one shared unit. Color, temperature, and shadows are common;
attenuation requires an all-local selection; cone angles require all Spot
Lights; rotation rejects any Point Light. **Approve & Apply Light** rejects the
entire group if one identity or property state is stale, applies one grouped
Editor transaction, and never saves the level automatically. One Ctrl+Z
restores all changed lights.

**Prepare Lighting Rig** accepts exactly five selected Actors: one non-light,
non-camera subject with observable bounds, one perspective standard Camera or
Cine Camera facing that subject, and exactly three Movable Point, Spot, or Rect
Lights that share one non-EV intensity unit. The model assigns the three exact
lights to Key, Fill, and Rim and chooses bounded contrast, palette, key side,
spacing, and brightness controls. Python computes all final positions, aim,
intensity ratios, attenuation, and Spot cone coverage. The dedicated
**Lighting Rig Action** card exposes every assignment and complete Before/After
evidence. **Approve & Apply Rig** revalidates all five Actors, changes only the
three lights in one transaction, never saves the level automatically, and is
restored by one Ctrl+Z.

After that approval, **Evaluate Applied Rig** temporarily captures one Lit PNG
from the frozen camera through the active perspective Level Editor viewport and
restores the user's view immediately. The configured local vision model returns
only categorical exposure, Fill, and Rim judgments. Python computes any fixed,
bounded intensity-only proposal. The separate **Lighting Rig Visual Review**
card requires approval and revalidates all five Actors before a second grouped
transaction. It never moves a light or changes the subject or camera.

**Prepare Camera** accepts one to 16 selected standard Camera Actors or Cine
Camera Actors. One selection uses the type-specific **Camera Action**. A 2-16
selection uses one shared restricted intent and shows complete ordered evidence
in **Coordinated Camera Action**. Both support world Transform, aperture, focus,
and exposure compensation. Standard Cameras expose FOV; Cine Cameras expose
focal length inside captured lens bounds. Approval rejects any stale identity,
lens, selection order, or property state, applies one Editor transaction, and
never saves the level automatically. Ctrl+Z restores the previous properties.

The secondary **Render & Evaluate** page starts the existing transient render
and visual-evaluation loop. That page is another capability inside the
assistant, not the identity of the whole product. Explicit targeting for
material slots, external CC0 material acquisition, one-to-32-Actor world/local
Transform, compatible one-to-16-Light group edits, single-Camera property
editing, and coordinated 2-16-camera property editing are connected. The
bounded Key/Fill/Rim rig is also connected. Selected native StaticMeshActor
evidence review plus approval-gated Cast Shadow and Max Draw Distance actions
are connected. Full Level Audit Stage 1 now scans the complete level, preserves
explicit exclusions, ranks every captured Actor, and reuses the same safe
approval/Undo executor for a bounded action subset. A separate read-only PIE/SIE
capture now records recomputable
frame, thread, GPU, process-memory, and RHI texture-memory evidence. A second
trace-backed workflow records a five-second `.utrace`, ranks timed GPU scopes,
and preserves the raw timeline for Unreal Insights. A repeatable selected-Actor
workflow now records two visible controls and three counterbalanced AB/BA/AB
pairs, restores the Actor, and aggregates scopes common to all eight traces.
One profiling-first orchestration now runs the static funnel, fixed runtime
baseline, GPU scope capture, and deterministic Top-2 Actor studies under one
validated PIE/SIE workload and produces one composite report. Its first
connected optimization handoff selects the best stable eligible Actor and runs
a separate deterministic eight-trace Cast Shadow on/off experiment. Runtime
state is restored before review; only an explicit, stale-checked approval after
PIE/SIE stops can apply the exact formal change as one unsaved Undo transaction.
That approval now arms **Post-Apply Shadow Verification**. Under a restarted,
identical PIE/SIE workload, the Assistant repeats the complete profiling chain,
hash-binds all three source reports, and recomputes fixed whole-level retain or
rollback guards. Keeping the edit makes no additional mutation; rollback needs
explicit approval, exact current-state revalidation, and a new bounded unsaved
transaction so unrelated Editor changes are not consumed by global Undo.
General geometry-aware arrangement, multi-shot visual evaluation, richer
shot-specific lighting tools, direct per-asset/material/shader/draw attribution, packaged-build
benchmark comparison, and asset-level performance fixes remain visibly marked
as not connected.

## Full Level Audit lifecycle

1. Open an Editor level. No Actor selection or PIE/SIE session is required.
2. Optionally enter a question. An empty prompt uses the default complete-level
   static audit request.
3. Press **Run Full Level Audit**.
4. Review captured and excluded counts, aggregate static evidence, the Top 5
   deterministic priorities, findings, and recommended deeper tests.
5. If the request explicitly produced supported setting actions, press
   **Approve & Apply Audit Actions**. Unreal revalidates every target; one stale
   Actor rejects the complete batch.
6. The level is never saved automatically. One Ctrl+Z restores the approved
   batch. Static priority must be confirmed with runtime/GPU evidence before it
   is treated as a measured bottleneck.

See [assistant_level_audits.md](assistant_level_audits.md) for scoring,
evidence limits, supported changes, and the next orchestration stage.

## Profiling-First Level Audit lifecycle

1. Start PIE or SIE with the representative camera, viewport, resolution, and
   workload already in their intended state.
2. Optionally enter a profiling question. An empty prompt uses the default
   complete-level bottleneck request.
3. Press **Run Profiling-First Level Audit** and keep the workload fixed.
4. The Assistant runs the read-only static audit, discards 30 warmup frames,
   captures 120 runtime samples, records one five-second GPU scope trace, and
   runs up to two deterministic eight-capture Actor studies.
5. If a selected static candidate cannot be mapped safely into the play world,
   its reason is recorded as a skip; the Assistant does not substitute a more
   convenient Actor.
6. Review frame P95, budget misses, the leading inclusive GPU scope, Actor-study
   outcomes relative to A/A control noise, limitations, and preserved evidence
   IDs. Ending PIE/SIE early makes the run fail rather than mixing workloads.
7. Cancel, failure, shutdown, and plugin unload restore temporary Actor runtime
   visibility. The formal level is never edited or saved by this run.

Profiling is the authority for performance. Images cannot measure frame or GPU
cost and are only auxiliary visual-regression guards. See
[assistant_level_profiling.md](assistant_level_profiling.md).

## Cast Shadow optimization lifecycle

1. Complete **Run Profiling-First Level Audit** under a representative fixed
   PIE/SIE workload.
2. Press **Test Best Shadow Optimization**. The Assistant selects the first
   stable profiling candidate whose formal component still casts shadows.
3. Keep the workload fixed while the Assistant records two shadow-on A/A
   controls and three counterbalanced shadow-on/off pairs. Every capture has a
   one-second warmup and a five-second trace.
4. Unreal restores the runtime shadow state before deterministic host analysis.
   A language or vision model cannot award the performance win.
5. Review the common-scope median, MAD, noise, votes, relative reduction, and
   fixed decision thresholds. Images may only inform human regression review.
6. If proposed, stop PIE/SIE. Press **Approve & Apply Shadow Optimization** or
   **Reject Shadow Optimization**. Approval revalidates the exact formal Actor,
   component, mesh, and Before state.
7. The level is not saved. One Ctrl+Z restores an approved Cast Shadow change.
8. After approval, restart the same representative PIE/SIE workload and press
   **Run Post-Apply Verification**. The complete profiling chain repeats with
   fresh report/run IDs and hashes.
9. Review frame P95, GPU P95, budget-miss, qualified-scope deltas, identity
   checks, and decision reasons. Press **Keep Optimization**, **Keep Anyway
   (Override Recommendation)**, or **Approve Rollback** as appropriate.
10. Rollback is available only after PIE/SIE stops and the exact Actor,
    component, mesh, and current `Cast Shadow = false` state revalidate. It is
    a new Undo-backed `false -> true` edit; the level is never saved.

See [assistant_shadow_optimization.md](assistant_shadow_optimization.md).
See
[assistant_shadow_optimization_verification.md](assistant_shadow_optimization_verification.md)
for the fixed comparison guards and evidence-freshness rules.

## Material action lifecycle

1. Select exactly one Actor with one valid Static Mesh Component.
2. If the mesh has multiple slots, choose the intended entry in **Target
   material slot**. Single-slot meshes need no manual choice.
3. Enter a material appearance request, such as `Make this look like dark,
   weathered wood.`
4. Press **Prepare Material**. The system writes local request and context
   evidence, embeds the request, and searches the validated material catalog.
5. Review the target Actor, slot, existing material, proposed asset path, and
   similarity score.
6. Press **Approve & Apply Material** to apply it, or **Reject** to discard it.
7. Use Ctrl+Z if the approved result is not wanted.

For an external material, use the same target and request, then:

1. Press **Search Poly Haven** and wait for four verified maps to be cached.
2. Review provider credit, source URL, CC0, resolution, five planned asset paths,
   and the 64-character approval SHA-256.
3. Press **Approve Import & Apply**. The button authorizes persistent creation
   of those five assets plus a transactional slot override.
4. Wait for import, scoped Asset Registry rescan, catalog backup/merge, and
   incremental Chroma synchronization to complete.

Changing the target slot while a search or proposal is active discards that
proposal. A new action must also be prepared if the target or current material
changes after proposal generation.

## Transform action lifecycle

1. Select between one and 32 Actors in the current level.
2. Enter one request such as `Move all selected Actors up by 50 cm` or `Move
   all selected Actors forward 100 cm in each Actor's local space.`
3. Press **Prepare Transform**. The Editor sorts by Actor path and freezes every
   identity, root component, mobility, lock/editability state, location,
   rotation, and scale. No scene mutation occurs.
4. Review the coordinate space, every target, changed channels, and complete
   world-space Before/After values in the **Transform Action** card.
5. Press **Approve & Apply Transform**, or **Reject** to make no change.
6. Use Ctrl+Z once to restore the complete previous selection. Save the level
   manually only if the result should persist.

Location uses centimeters; rotation maps `x/y/z` to Unreal Roll/Pitch/Yaw in
degrees. World space supports absolute and additive location/rotation. Local
space supports additive location/rotation only: translation is rotated by each
Actor's basis, and rotation is quaternion-composed with each Actor's captured
orientation. The same intent is applied uniformly to the ordered selection.
Geometry-aware placement and relational arrangements resolve as **Unresolved**
instead of guessing.

## Light action lifecycle

1. Select between one and 16 Directional, Point, Spot, or Rect Lights. Every
   selected Actor must be a supported Light.
2. Enter one request such as `Make all selected lights 20% brighter` or, for an
   all-Spot selection, `Set all selected lights to 3200 K and widen their outer
   cones to 45 degrees.`
3. Press **Prepare Light**. The Editor sorts the selection by Actor path and
   freezes every Actor/component identity, Mobility, light kind, intensity unit,
   editability and lock state, and supported property. No scene mutation occurs.
4. Review the compatibility result, changed-property list, and complete
   per-light Before/After state in the **Light Action** card. Unchanged lights
   remain visible as evidence.
5. Press **Approve & Apply Light**, or **Reject** to make no change. Approval
   revalidates the complete group; one stale light rejects all changes.
6. Use Ctrl+Z once to restore every changed light. Save the level manually only
   if the result should persist.

The intensity unit is never converted or changed by this action. Directional
Lights require lux; local lights retain their captured lumens, candelas,
unitless, EV, or nits setting. Relative intensity edits may span mixed non-EV
units, but EV multiplication and mixed-unit absolute changes are rejected.
Point Light rotation is rejected because it has no visual meaning. Requests to
spawn, delete, rename, retype, or give arbitrary distinct per-light property
instructions resolve as **Unresolved**. Use **Prepare Lighting Rig** for the
bounded Key/Fill/Rim workflow.

## Lighting rig action lifecycle

1. Select exactly five Actors: one subject, one perspective Camera Actor or
   Cine Camera Actor, and exactly three compatible Movable Point, Spot, or Rect
   Lights. The lights must use one shared non-EV intensity unit.
2. Enter one request such as `Create a warm cinematic three-point lighting rig
   around this subject, with the key on camera left and dramatic contrast.`
3. Press **Prepare Lighting Rig**. The Editor identifies each Actor by type,
   sorts and freezes the three light identities, captures the subject Transform
   and bounds, and captures the camera view. No scene mutation occurs.
4. Review the semantic style, Key/Fill/Rim assignments, changed properties, and
   complete per-light Before/After values in the **Lighting Rig Action** card.
5. Press **Approve & Apply Rig**, or **Reject** to make no change. Approval
   revalidates the subject, camera, and all three lights; one stale Actor rejects
   the complete rig.
6. Optionally press **Evaluate Applied Rig** while a perspective Level Editor
   viewport is active. One Lit PNG is captured from the frozen camera; the
   viewport is restored before the local vision model runs.
7. A balanced result passes without a new action. Otherwise, review the three
   intensity-only Before/After values and press **Approve Intensity
   Correction**, or **Reject**.
8. One Ctrl+Z removes an approved visual correction; a second Ctrl+Z restores
   the original three lights. Save the level manually only if the result should
   persist.

Selection order does not assign roles. The model assigns exact frozen paths,
while Python computes deterministic camera-relative numeric results from the
subject bounds and camera basis. The subject and camera remain unchanged.
Directional, Static, Stationary, EV, mixed-unit, orthographic, light-spawning,
and multi-camera rig requests are rejected rather than approximated.

## Camera action lifecycle

1. Select exactly one Camera Actor or Cine Camera Actor.
2. Enter a numeric request such as `Set this Cine Camera to 85 mm, f/4, focus
   manually at 350 cm, and brighten exposure compensation by 1 EV.`
3. Press **Prepare Camera**. The Editor freezes Actor/component identity,
   camera kind, projection mode, editability/lock state, lens bounds, Transform,
   lens/focus properties, exposure compensation, and Post Process blend weight.
   No scene mutation occurs.
4. Review the changed-property list and complete type-specific Before/After
   state in the **Camera Action** card.
5. Press **Approve & Apply Camera**, or **Reject** to make no change.
6. Use Ctrl+Z to restore the previous properties. Save the level manually only
   if the result should persist.

Standard Cameras expose FOV and never focal length. Cine Cameras expose focal
length within the captured lens range and never direct FOV. Filmback, lens
preset, aspect ratio, projection mode, scale, tracking targets, and Post
Process blend weight are not changed. Orthographic or multi-camera requests and
requests that require unsupported composition reasoning resolve as
**Unresolved**.

For a coordinated action, select 2-16 Camera/Cine Camera Actors and use the same
**Prepare Camera** button. Relative edits are computed from each frozen Before
state; absolute edits converge on one target. Review every target in the
**Coordinated Camera Action** card, then press **Approve & Apply Cameras** or
**Reject**. Mixed standard/Cine selections can use shared properties, but direct
FOV or focal-length edits resolve as **Unresolved**. One stale camera rejects the
whole batch, and one Ctrl+Z restores the complete approved selection.

## Render and Evaluate lifecycle

The Render & Evaluate page exposes the complete bounded workflow:

1. accept a scene or product-render request;
2. retrieve only assets present in the configured catalog;
3. ask the local planning model for a schema-valid `RenderSpec`;
4. run deterministic preflight checks;
5. launch the existing headless Unreal preview adapter;
6. evaluate the PNG with the configured local vision model;
7. apply a bounded correction or record an explicit stop reason.

The six stage chips display retrieval, planning, validation, rendering,
evaluation, and correction state. The right side displays the newest
`beauty.png`, the latest evaluation summary, and deterministic image metrics.
The process log is bounded to the current run so a long session cannot grow the
Editor widget indefinitely.

## Source and local installation

The canonical source belongs in this repository:

```text
unreal/RenderMasterBot/
|-- RenderMasterBot.uplugin
`-- Source/RenderMasterBotEditor/
```

For a local Unreal project, copy that complete folder to:

```text
<Project>/Plugins/RenderMasterBot
```

Enable the Editor-only plugin in the `.uproject` descriptor:

```json
{
  "Name": "RenderMasterBot",
  "Enabled": true,
  "TargetAllowList": ["Editor"]
}
```

Compile the project's Editor target, then open **Tools > RenderMasterBot >
RenderMasterBot Assistant**. Passing `-RenderMasterOpenPanel` to `UnrealEditor.exe`
opens the tab one second after Editor startup and is useful for smoke testing.

## Configuration

The collapsible **Runtime settings** section stores three workstation-local
paths in `EditorPerProjectUserSettings.ini`:

- Python executable: the virtual environment interpreter that can import the
  editable `render_master_bot` package;
- asset catalog: a validated `asset_cards.json` produced from the target
  project;
- workflow output root: a local directory for prompts, manifests, previews,
  model evidence, and logs.

The first launch can be preconfigured with environment variables:

```text
RENDERMASTER_PYTHON
RENDERMASTER_ASSET_CATALOG
RENDERMASTER_WORKFLOW_ROOT
```

Environment values take precedence for that Editor process. The engine root
and `.uproject` path are discovered from the running Editor rather than entered
by the user.

## Process and data safety

Prompt text is never concatenated into a shell command. The panel writes a
UTF-8 request file and uses the CLI's mutually exclusive `--prompt-file`
argument. The CLI rejects missing or empty files, files above 64 KiB, and
prompts above 4,000 characters.

Material proposals are also persisted as validated JSON outside Git. Retrieval
results are rechecked against the supplied `AssetCard` catalog before a proposal
is emitted. External preparation accepts only trusted Poly Haven endpoints and
freezes four map hashes plus five target paths. Preparing or rejecting does not
modify Unreal Content; external approval must match the exact proposal SHA-256.
`SetMaterial` runs inside `FScopedTransaction` in both paths.

Transform requests use a separate strict intent schema. The model describes
only one allowed intent; Python computes and bounds every Actor's final
world-space After values while the Editor owns ordered target and Before
evidence. Apply-time revalidation prevents a stale Actor from causing a partial
batch. All changed Actors are applied inside one `FScopedTransaction`; a
mid-apply failure restores earlier Actors and cancels the transaction.

Light requests use their own strict intent schema and type matrix. The model
does not control the target, Before evidence, final changed-property list, or
intensity unit. Python computes bounded After values. Unreal then revalidates
the Actor path, class, GUID, component name, kind, unit, lock/editability state,
and complete Before snapshot. Approved properties are edited through the same
transactional Editor path used for Static, Stationary, and Movable lights,
followed by normal post-edit and render-state notifications.

Lighting-rig requests use a separate strict role/style intent. The model can
assign only the three captured local-light paths to Key, Fill, and Rim exactly
once and select one value for contrast, palette, key side, spacing, and
brightness. It cannot author trusted geometry or final numbers. Python derives
world positions from frozen subject bounds and the captured camera basis, aims
Spot and Rect Lights at the subject, preserves Point rotation, and computes
bounded intensity ratios, attenuation, cone coverage, and changed-property
evidence. Unreal revalidates all five Actors before applying the three lights in
one rollback-safe `FScopedTransaction`.

Visual rig review uses another strict intent that contains only categorical
exposure, Fill-balance, and Rim-separation judgments. The PNG hash, pixel
statistics, frozen Actor evidence, and final numeric values are host-owned.
Python maps categories to fixed 1.2, 1.0, or `1 / 1.2` factors and can change
only intensity. Unreal matches the proposal to the captured PNG, then
revalidates the subject, camera, and all three lights before applying one
separate transaction. The review cannot loop after a pass or proposal.

Camera requests use a separate strict intent schema and standard/Cine type
matrix. The model does not control the selected target, Before evidence, lens
bounds, or final changed-property list. Python computes bounded After values.
Unreal revalidates Actor/component identity, camera kind, perspective
projection, editability/lock state, lens bounds, and every captured property
immediately before applying the approved transaction. A zero Post Process blend
weight causes focus or exposure requests to fail safely instead of silently
changing the blend weight.

Performance requests use a separate strict intent schema and measured
StaticMeshActor evidence. Findings must cite captured fields. Python turns an
explicit supported edit into complete ordered Before/After actions; review-only
diagnosis has no Apply button. Unreal rechecks Actor/component/mesh identity,
LOD0 triangles, LOD count, materials, Nanite, collision, Mobility, Tick, bounds,
Cast Shadow, and Max Draw Distance before one rollback-safe grouped transaction.
Only Cast Shadow and Max Draw Distance are executable in this milestone.

**Capture Runtime Performance** requires an active PIE or SIE world and an
explicit question. The host discards 30 warmup frames, then stores 120 raw
consecutive frame, Game Thread, Render Thread, optional RHI Thread, and optional
GPU samples. It computes nearest-rank timing summaries, budget misses, and the
largest available P95 component, then records whole-Editor process memory and
RHI texture-memory evidence. Python recomputes the summaries from the raw
samples before the local model can review them. The resulting **Runtime
Performance Capture** card is permanently read-only: it has no Apply action and
cannot attribute cost to a pass, Actor, asset, or packaged build without later
profiler evidence.

**Capture GPU Scope Trace** requires an active PIE or SIE world and an explicit
question. The Assistant refuses to interrupt any trace already owned by Unreal
Insights or another tool. It records five seconds of `cpu`, `gpu`, `frame`, and
`bookmark` channels, preserves the `.utrace`, and asks UE 5.7 TraceServices to
enumerate timed GPU queues and their scope events. The host ranks the top 64
queue-local scopes by accumulated inclusive duration and retains count, mean,
maximum, and depth evidence. Python permits the local model to cite only exact
captured `scope_id` values. The resulting **Unreal Insights GPU Scope Review**
is read-only; **Open Trace in Unreal Insights** launches the authoritative raw
timeline. Inclusive totals may overlap, and the workflow does not infer an
Actor, asset, material, shader, draw call, or packaged-build cause.

**Run Repeatable Selected Actor GPU Study** requires an active PIE or SIE world,
an explicit question, and exactly one visible selected Actor with at least one
registered primitive component. Selection may point to the Editor Actor or its
runtime counterpart; Unreal resolves and records both identities. The workflow
uses fixed `AA_AB_BA_AB` order: two visible A/A controls, then AB, BA, AB
visible/hidden pairs. Each of the eight five-second captures starts after a
one-second warmup.

The runtime Actor is restored immediately after the final hidden trace stops,
before analysis. Restoration also runs on cancel, failure, dismissal, Editor
shutdown, and plugin unload. Python intersects queue-local scope identity across
all eight Top-64 sets and recomputes A/A noise, three paired deltas, median,
MAD, direction votes, signal excess, and classification from raw evidence. The
**Repeatable Selected Actor GPU Study** card exposes **Open First A/A Control
Trace** and **Open Final Hidden Trace**; all eight files remain preserved. The
formal level is never edited or saved. Results remain associations, not direct
pass/draw/material/shader/asset/packaged-build causation. See
[assistant_actor_gpu_impact_studies.md](assistant_actor_gpu_impact_studies.md).

The composite **Profiling-First Level Audit** controller runs these existing
read-only stages sequentially. Its final Python synthesis makes no additional
model call: it validates workload identity, child-report IDs and hashes, fixed
capture parameters, deterministic candidate order, restoration, and
recomputable summary metrics before Unreal accepts the report.

The controller belongs to the plugin module, so closing and reopening the tab
does not kill an active workflow. Pressing **Cancel**, closing the Editor, or
unloading the module terminates Python together with its child Unreal process
tree. Runtime output stays under the configured local workflow root and is not
copied into the source repository.

The panel treats `workflow_manifest.json` as lifecycle truth. Console output is
displayed for diagnosis but is not parsed to invent stage or success state.

## Verification

Build the real Editor target:

```powershell
& "E:\Unreal Engine\UE_5.7\Engine\Build\BatchFiles\Build.bat" `
  OptimizationPluginEditor Win64 Development `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  -WaitMutex -NoHotReloadFromIDE
```

Run all Editor-side contract parsers inside a real, headless Editor process:

```powershell
& "E:\Unreal Engine\UE_5.7\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  -unattended -nop4 -nullrhi -nosplash `
  '-ExecCmds=Automation RunTests RenderMasterBot.Editor;Quit' `
  '-TestExit=Automation Test Queue Empty' -log
```

The Editor automation suite covers running and terminal manifest parsing,
project and external material proposals, single and batch Transform, Light,
lighting-rig, lighting-rig-review, Camera, selected-mesh performance,
runtime-performance, Insights GPU report parsing and aggregation, single-pair
Actor GPU comparison/report validation, repeatable-study report parsing, and
profiling-first composite report parsing/tamper rejection, deterministic shadow
optimization proposal parsing/threshold enforcement/tamper rejection,
post-apply shadow verification retain/rollback/unresolved parsing plus
metric/decision/threshold tamper rejection,
an actual two-Actor Transform batch
followed by one Unreal Undo, the original single-Actor Transform regression, a
Stationary Light property apply followed by Unreal Undo, a three-light rig
apply followed by one Unreal Undo, and a Camera property apply followed by
Unreal Undo.
A visible smoke test should also confirm that the panel appears, both search
buttons produce the expected proposal type, approval changes only the chosen
slot, **Prepare Transform** shows every selected Actor and correct local/world
Before/After values, all four scene-action types undo correctly, **Prepare
Light** exposes only type-valid fields and undoes correctly, **Prepare Camera**
distinguishes FOV from focal length and undoes correctly, **Prepare Lighting
Rig** shows exact Key/Fill/Rim roles and one grouped Undo, **Evaluate Applied
Rig** restores the visible viewport and creates at most one intensity-only
correction, **Run Repeatable Selected Actor GPU Study** restores the runtime
Actor and preserves eight traces, **Run Profiling-First Level Audit** stays on
one fixed PIE/SIE workload and exposes one composite result, **Test Best Shadow
Optimization** restores the runtime component and never exposes approval until
PIE/SIE has stopped, **Run Post-Apply Verification** accepts only the restarted
matching workload and exposes rollback only after an independently recomputed
recommendation, and no
RenderMasterBot-specific error is written during startup.

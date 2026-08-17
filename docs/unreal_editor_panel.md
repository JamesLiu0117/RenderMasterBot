# Unreal Editor panel

`unreal/RenderMasterBot` is the native Slate workspace for the RenderMasterBot
graphics assistant. It is an Editor-only plugin: it does not ship model
weights, start a network service, or add runtime code to a packaged game.

## Workspace model

The default **Assistant** page is the product entry point. It reads the live
project name, current level, and selected actors directly from the Editor. The
operator can inspect that context, prepare a bounded action, review its scope,
and explicitly approve or reject it. Context inspection and proposal generation
do not modify the Editor scene.

The connected direct Editor actions are material selection, one-to-32-Actor
world/local Transform editing, compatible one-to-16-Light group property
editing, and one-Camera property editing.
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

**Prepare Camera** accepts exactly one selected standard Camera Actor or Cine
Camera Actor. Both support world Transform, aperture, focus, and exposure
compensation. Standard Cameras expose FOV; Cine Cameras expose focal length
inside the captured lens bounds. Its **Camera Action** card shows the exact
type-specific Before/After state. **Approve & Apply Camera** rejects stale
identity, lens, or property state, applies one Editor transaction, and never
saves the level automatically. Ctrl+Z restores the previous properties.

The secondary **Render & Evaluate** page starts the existing transient render
and visual-evaluation loop. That page is another capability inside the
assistant, not the identity of the whole product. Explicit targeting for
material slots, external CC0 material acquisition, one-to-32-Actor world/local
Transform, compatible one-to-16-Light group edits, and single-Camera property
editing are connected. Geometry-aware arrangement, role-specific multi-light
coordination, coordinated multi-camera operations, and performance diagnosis
remain visibly marked as not connected.

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
spawn, delete, rename, retype, or give different selected lights distinct
Key/Fill/Rim responsibilities resolve as **Unresolved**.

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

Camera requests use a separate strict intent schema and standard/Cine type
matrix. The model does not control the selected target, Before evidence, lens
bounds, or final changed-property list. Python computes bounded After values.
Unreal revalidates Actor/component identity, camera kind, perspective
projection, editability/lock state, lens bounds, and every captured property
immediately before applying the approved transaction. A zero Post Process blend
weight causes focus or exposure requests to fail safely instead of silently
changing the blend weight.

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
project and external material proposals, single and batch Transform, Light, and
Camera proposal parsing, an actual two-Actor Transform batch followed by one
Unreal Undo, the original single-Actor Transform regression, a Stationary Light
property apply followed by Unreal Undo, and a Camera property apply followed by
Unreal Undo.
A visible smoke test should also confirm that the panel appears, both search
buttons produce the expected proposal type, approval changes only the chosen
slot, **Prepare Transform** shows every selected Actor and correct local/world
Before/After values, all four scene-action types undo correctly, **Prepare
Light** exposes only type-valid fields and undoes correctly, **Prepare Camera**
distinguishes FOV from focal length and undoes correctly, and no
RenderMasterBot-specific error is written during startup.

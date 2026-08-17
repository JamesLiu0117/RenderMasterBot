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

The connected direct Editor actions are material selection, one-Actor world
Transform editing, and one-Light property editing. Material selection supports one
selected Actor that contains exactly one valid Static Mesh Component. A single
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

**Prepare Transform** accepts one selected Actor and a world-space translation,
rotation, or scale request. It does not depend on the Actor having a Static Mesh.
The resulting **Transform Action** card shows the frozen target and exact
Before/After values. **Approve & Apply Transform** rechecks that the same Actor
still exists and has not moved since proposal generation, then applies one
`FScopedTransaction`. It never saves the level automatically; Ctrl+Z restores
the complete previous Transform.

**Prepare Light** accepts exactly one selected Directional, Point, Spot, or
Rect Light. Its **Light Action** card shows the frozen light type and intensity
unit plus the complete Before/After state. The available fields are
type-specific: intensity, color, temperature, and shadows are common;
attenuation belongs to local lights; cone angles belong only to Spot Lights;
rotation belongs to Directional, Spot, and Rect Lights. **Approve & Apply
Light** rejects stale identity or property state, applies one Editor
transaction, and never saves the level automatically. Ctrl+Z restores the
previous properties.

The secondary **Render & Evaluate** page starts the existing transient render
and visual-evaluation loop. That page is another capability inside the
assistant, not the identity of the whole product. Explicit targeting for
material slots, external CC0 material acquisition, and single-Actor world
Transform and single-Light property editing are connected.
Multi-Actor/local-space Transform editing, coordinated multi-light operations,
camera properties, and performance diagnosis remain visibly marked as not connected.

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

1. Select exactly one Actor in the current level.
2. Enter a world-space request such as `Move this Actor up by 50 cm and rotate
   it right by 30 degrees.`
3. Press **Prepare Transform**. The Editor freezes path, class, GUID, lock and
   editability state, plus location, rotation, and scale. No scene mutation
   occurs.
4. Review the target, changed channels, and complete Before/After values in the
   **Transform Action** card.
5. Press **Approve & Apply Transform**, or **Reject** to make no change.
6. Use Ctrl+Z to restore the previous Transform. Save the level manually only
   if the result should persist.

Version 0.1 intentionally accepts world space only. Location uses centimeters;
rotation maps `x/y/z` to Unreal Roll/Pitch/Yaw in degrees. Requests that require
local space, geometry-aware placement, multi-Actor coordination, or another
ambiguous capability resolve as **Unresolved** instead of guessing.

## Light action lifecycle

1. Select exactly one Directional, Point, Spot, or Rect Light.
2. Enter a request such as `Make this Spot Light 20% brighter, set it to 3200 K,
   widen the outer cone to 45 degrees, and rotate it right by 30 degrees.`
3. Press **Prepare Light**. The Editor freezes Actor/component identity, light
   kind, intensity unit, editability and lock state, and every supported
   property. No scene mutation occurs.
4. Review the changed-property list and complete Before/After state in the
   **Light Action** card.
5. Press **Approve & Apply Light**, or **Reject** to make no change.
6. Use Ctrl+Z to restore the previous properties. Save the level manually only
   if the result should persist.

The intensity unit is never converted or changed by this action. Directional
Lights require lux; local lights retain their captured lumens, candelas,
unitless, EV, or nits setting. Point Light rotation is rejected because it has
no visual meaning. Requests to spawn, delete, rename, retype, or coordinate
multiple lights resolve as **Unresolved**.

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
only allowed per-axis operations; Python computes and bounds After values, while
the Editor owns target and Before evidence. Apply-time identity and Transform
revalidation prevents a stale proposal from overwriting a user's intervening
edit. The final `SetActorTransform` also runs inside `FScopedTransaction`.

Light requests use their own strict intent schema and type matrix. The model
does not control the target, Before evidence, final changed-property list, or
intensity unit. Python computes bounded After values. Unreal then revalidates
the Actor path, class, GUID, component name, kind, unit, lock/editability state,
and complete Before snapshot. Approved properties are edited through the same
transactional Editor path used for Static, Stationary, and Movable lights,
followed by normal post-edit and render-state notifications.

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
project and external material proposals, Transform and Light proposal parsing,
an actual transient-Actor Transform apply followed by Unreal Undo, and a
Stationary Light property apply followed by Unreal Undo.
A visible smoke test should also confirm that the panel appears, both search
buttons produce the expected proposal type, approval changes only the chosen
slot, **Prepare Transform** shows correct Before/After values, all three scene
actions undo correctly, **Prepare Light** exposes only type-valid fields and undoes
correctly, and no RenderMasterBot-specific error is written during startup.

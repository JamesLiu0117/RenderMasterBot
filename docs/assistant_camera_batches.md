# Coordinated multi-camera actions

The Unreal Assistant can prepare one shared, bounded edit for an ordered
selection of 2-16 standard Camera Actors or Cine Camera Actors. This workflow
is for changes that should be coordinated across existing shots, such as
raising every camera by 50 cm, adding 10 degrees of yaw, matching exposure
compensation, or setting a common compatible lens value.

It is not a per-shot composition system. The model interprets one restricted
intent; Python computes the exact After state independently from every frozen
Before value; Unreal owns identity capture, approval, stale-state validation,
application, and Undo.

## Editor lifecycle

1. Select 2-16 Camera/Cine Camera Actors in one level.
2. Enter one shared request, for example `Raise every selected camera by 50 cm
   and brighten exposure compensation by 1 EV.`
3. Press **Prepare Camera**. The Editor sorts the selection by Actor path and
   freezes every Actor/component identity, camera kind, projection mode, lens
   bounds, Transform, focus state, exposure state, and Post Process blend
   weight. Nothing changes in the level.
4. Review every action in **Coordinated Camera Action**. A camera that already
   matches an absolute target remains present as an explicit no-op.
5. Press **Approve & Apply Cameras**, or **Reject**.
6. Approval revalidates the complete frozen selection before any mutation. One
   stale, missing, locked, reordered, or type-changed camera rejects the whole
   batch.
7. A successful approval is one Unreal transaction. Ctrl+Z once restores the
   complete selection. The level is never saved automatically.

## Supported shared edits

- world location and rotation using `set` or `add`;
- standard Camera FOV using `set`, `add`, or `multiply`;
- Cine Camera focal length within each frozen lens range;
- aperture within each captured range;
- manual/disabled focus mode and bounded focus distance;
- exposure-compensation enable state and EV value.

Relative Transform operations are computed from each camera's own Before
state, so `move every camera up 50 cm` preserves the spacing between shots.
Absolute operations deliberately converge cameras on one value.

A mixed standard/Cine selection can use properties shared by both kinds.
Direct FOV or focal-length requests for a mixed selection resolve as
**Unresolved**, because applying a type-specific property to only part of the
selection would violate all-or-nothing behavior.

## Safety boundary

- The model returns one `CameraEditIntent`; it never returns Actor paths,
  Before evidence, or a variable list of camera actions.
- The host compiles that intent against every `UnrealCameraContext` and emits
  a complete ordered `AssistantCameraBatchProposal`.
- The C++ parser reuses the single-camera type and numeric validator for every
  nested action and rejects target-order or evidence tampering.
- Application preserves scale, projection mode, filmback, lens presets,
  aspect ratio, tracking targets, and Post Process blend weight.
- If an unexpected Unreal write fails after another camera was changed, the
  batch helper restores the already-written cameras and cancels the transaction.

## Deliberate limits

- no camera creation, deletion, renaming, or shot sequencing;
- no different natural-language instruction per named camera;
- no automatic look-at target or tracking-focus assignment;
- no geometry-, visibility-, or occlusion-aware reframing;
- no visual review across all resulting shots yet;
- no level save.

Those require a later geometry-aware arrangement and multi-shot evaluation
layer rather than a wider model permission boundary.

## CLI and contracts

The Editor runs the equivalent of:

```powershell
render-master assistant-camera-batch-propose `
  --prompt-file request.txt `
  --context camera_selection_context.json `
  --proposal-id camera_batch_001 `
  --output camera_batch_proposal.json
```

Validate the repository example and export the proposal schema with:

```powershell
render-master validate examples/unreal_camera_selection_context.json `
  --contract unreal-camera-selection-context

render-master schema assistant-camera-batch-proposal `
  -o assistant_camera_batch_proposal.schema.json
```

All request, selection, proposal, and process-log evidence remains under the
configured workflow root at `assistant-camera-batch/<proposal-id>/`, outside
Git.

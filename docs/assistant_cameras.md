# Approval-gated Camera and Cine Camera actions

The Unreal Assistant can prepare and apply one bounded property change for
exactly one selected `CameraActor` or `CineCameraActor`. Preparing a proposal is
read-only. The level changes only after the operator reviews the dedicated
**Camera Action** card and presses **Approve & Apply Camera**.

## Supported properties

| Property | Standard Camera | Cine Camera | Units |
| --- | --- | --- | --- |
| World location | set or add | set or add | centimeters |
| World rotation | set or add | set or add | degrees |
| Field of view | set, add, or multiply | not exposed directly | degrees |
| Focal length | not exposed | set, add, or multiply within the captured lens | millimeters |
| Aperture | set, add, or multiply | set, add, or multiply within captured bounds | f-stop |
| Focus mode | project default or manual | project default, manual, tracking, or disabled state is preserved; new intents choose manual or disabled | enum |
| Manual focus distance | set, add, or multiply | set, add, or multiply | centimeters |
| Exposure compensation | enable/disable, set, or add | enable/disable, set, or add | EV |

The first version intentionally does not change Filmback, lens presets, aspect
ratio, projection mode, scale, tracked focus targets, or Post Process blend
weight. Orthographic cameras, multi-camera coordination, shot sequencing, and
requests that require visual composition judgment are reported as
**Unresolved** instead of being approximated.

## Why the model does not own the final camera state

The local planner returns only a restricted `CameraEditIntent`. The Editor owns
the selected Actor and captures its complete Before state. Python then applies
the allowed operations, enforces type-specific and numeric bounds, and creates
the final `AssistantCameraProposal` with an exact changed-property list.

For a Cine Camera, the proposal must remain inside the lens's captured focal
length, aperture, and minimum-focus bounds. A standard Camera can change FOV but
cannot contain a focal-length edit. A Cine Camera can change focal length but
cannot contain a direct FOV edit. Exposure compensation is limited to -15 to
+15 EV, focus distance is bounded, and world location follows the same safety
limit as the single-Actor Transform action.

If Post Process blend weight is zero, a focus or exposure request is rejected
because the requested visual change would have no effect. The action never
silently changes blend weight to make itself visible.

## Approval and stale-state boundary

Before planning, the Editor freezes:

- level, Actor name, path, class, and GUID;
- Camera Component name, camera kind, mobility, projection mode, editability,
  and location-lock state;
- Cine lens focal/aperture bounds and minimum focus distance;
- world Transform, FOV or focal length, aperture, focus, exposure compensation,
  and Post Process blend weight.

Immediately before approval, Unreal reads all of those values again. If the
camera was replaced, renamed, locked, retyped, moved, given a different lens,
or edited while the proposal was open, application fails and a new proposal is
required. Successful approval applies one `FScopedTransaction`, runs normal
post-edit and render-state notifications, and redraws the viewport. The level
is marked dirty but never saved automatically; Ctrl+Z restores the prior state.

## UI workflow

1. Select exactly one Camera Actor or Cine Camera Actor.
2. Enter a numeric request, for example: `Set this Cine Camera to 85 mm, f/4,
   focus manually at 350 cm, and brighten exposure compensation by 1 EV.`
3. Press **Prepare Camera**.
4. Review the exact target, camera kind, changed properties, lens limits, and
   complete Before/After values in the **Camera Action** card.
5. Press **Approve & Apply Camera**, or **Reject** to make no level change.
6. Use Ctrl+Z to restore the previous properties. Save the level manually only
   if the result should persist.

## CLI and contracts

The read-only proposal boundary can be tested without launching Unreal:

```powershell
render-master assistant-camera-propose `
  --prompt "Set this Cine Camera to 85 mm and f/4" `
  --context examples/unreal_camera_context.json `
  --proposal-id camera_example_001 `
  --output camera_proposal.json
```

Export or validate the public boundary:

```powershell
render-master schema unreal-camera-context
render-master schema assistant-camera-proposal
render-master validate examples/unreal_camera_context.json `
  --contract unreal-camera-context
```

Local request, context, proposal, and process logs are written below the
configured workflow root under `assistant-camera/<proposal-id>/`; they remain
outside Git.

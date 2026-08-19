# Approval-gated Light group property actions

The Unreal Assistant can prepare and apply one bounded property intent to an
ordered selection of one to 16 Directional, Point, Spot, or Rect Lights.
Proposal generation is read-only. The level changes only after the operator
reviews every light in the **Light Action** card and presses **Approve & Apply
Light**.

This action provides reliable compatible group property editing. The separate
**Prepare Lighting Rig** action coordinates exactly three compatible local
lights as camera-relative Key, Fill, and Rim; see
`assistant_lighting_rigs.md`.

## Supported property matrix

| Property | Directional | Point | Spot | Rect | Units or range |
| --- | --- | --- | --- | --- | --- |
| Intensity | yes | yes | yes | yes | captured Unreal unit; never converted |
| Linear RGB color | yes | yes | yes | yes | each channel 0–1 |
| Color temperature | yes | yes | yes | yes | 1,000–20,000 K |
| Cast shadows | yes | yes | yes | yes | enabled or disabled |
| Attenuation radius | no | yes | yes | yes | centimeters |
| Inner/outer cone | no | no | yes | no | 0–89 degrees; inner ≤ outer |
| Rotation | yes | no | yes | yes | Unreal Roll/Pitch/Yaw degrees |

One intent is applied uniformly to the complete frozen selection. The whole
group must support every requested property:

- intensity `multiply` works across mixed non-EV units, so “20% brighter” can
  safely include lux and lumens while preserving both units;
- intensity `set` or `add` requires one shared frozen unit across the selection;
- attenuation requires every selected light to be Point, Spot, or Rect;
- cone changes require an all-Spot-Light selection;
- rotation rejects a selection containing any Point Light;
- EV intensity supports set/add but not multiply.

An incompatible selection becomes **Unresolved** or fails the bounded retry. No
light is silently skipped. A light that already satisfies the requested value
remains visible as a no-op action, but at least one selected light must change.

## Why the model does not own the final state

The local planner returns one `LightEditIntent`, not Unreal commands or trusted
per-light values. Python checks the selection-wide type/unit matrix, applies the
intent to every Editor-owned Before snapshot, computes each complete After
snapshot, and derives the exact changed-property list.

The host rejects non-finite values, negative non-EV intensity, excessive
intensity or edit deltas, invalid temperature, invalid cone relationships,
meaningless Point Light rotation, incompatible group fields, whole-group no-op
results, and model output that disagrees with deterministic evidence.

The public `AssistantLightBatchProposal` preserves the complete ordered
selection and one action for every light. The original single-light contracts
and CLI remain available for backward compatibility.

## Approval boundary

Before planning, the Editor sorts the selection by Actor path and freezes, for
every light:

- project, level, Actor label, path, class, and GUID;
- component name, Mobility, light kind, editability, and lock state;
- intensity unit and complete type-specific property snapshot.

Immediately before approval, Unreal rereads every Actor and component. A rename,
replacement, type/unit/Mobility change, property edit, lock, or deletion on any
light invalidates the complete proposal before a transaction begins.

Successful approval applies all changed lights inside one `FScopedTransaction`
through the Editor property-edit path used by Static, Stationary, and Movable
lights. A rotation failure restores earlier group edits and cancels the
transaction. Post-edit and render-state notifications run afterward. The level
is marked dirty but never saved automatically; one Ctrl+Z restores the group.

## UI workflow

1. Select between one and 16 supported Light Actors.
2. Enter one compatible group request, for example:
   - `Make all selected lights 20% brighter.`
   - `Set all selected lights to 4500 K and enable shadows.`
3. Press **Prepare Light**. No Editor scene mutation occurs.
4. Review every target, kind, unit, changed property, and complete Before/After
   state.
5. Press **Approve & Apply Light**, or **Reject**.
6. Use Ctrl+Z once if the complete group should be restored.

Spawn/delete/rename/retype requests, arbitrary per-light instructions, and
unsupported or incompatible property combinations resolve as **Unresolved**.
Use **Prepare Lighting Rig** for the bounded Key/Fill/Rim workflow.

## CLI and contracts

Exercise the group proposal boundary without mutating Unreal:

```powershell
render-master assistant-light-batch-propose `
  --prompt "Make all selected lights 20% brighter" `
  --context examples/unreal_light_selection_context.json `
  --proposal-id light_batch_example_001 `
  --output light_batch_proposal.json
```

Export or validate the public boundary:

```powershell
render-master schema unreal-light-selection-context
render-master schema assistant-light-batch-proposal
render-master validate examples/unreal_light_selection_context.json `
  --contract unreal-light-selection-context
```

The Editor writes request, selection context, proposal, and process logs below
the configured workflow root at `assistant-light-batch/<proposal-id>/`. These
local artifacts remain outside Git.

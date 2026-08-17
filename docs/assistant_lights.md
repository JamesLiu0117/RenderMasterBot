# Approval-gated Light property actions

The Unreal Assistant can prepare and apply one bounded property change for
exactly one selected Directional, Point, Spot, or Rect Light. Proposal
generation is read-only. The level changes only after the operator reviews a
dedicated **Light Action** card and presses **Approve & Apply Light**.

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

Directional Light intensity is expressed in lux. Local lights preserve their
captured lumens, candelas, unitless, EV, or nits setting. A percentage request
uses a bounded multiplier except for EV, where additive stops are meaningful
and multiplication is rejected. A direct RGB request disables temperature
unless the request explicitly says otherwise; a Kelvin request enables it.

## Why the model does not own the final state

The local planner returns a `LightEditIntent`, not an Unreal command. It can
request only allowed operations for the captured light kind. Python applies
those operations to the Editor-owned Before snapshot, clamps the capability to
documented bounds, computes the complete After snapshot, and derives the exact
changed-property list.

The host rejects non-finite values, negative non-EV intensity, excessive
intensity or edit deltas, invalid temperature, invalid cone relationships,
meaningless Point Light rotation, type-incompatible fields, no-op results, and
model output that disagrees with the deterministic changed-property list.

## Approval boundary

Before planning, the Editor freezes:

- project, level, Actor name, path, class, and GUID;
- light component name and light kind;
- editability, lock state, and intensity unit;
- rotation, intensity, linear RGB color, temperature state/value, shadows, and
  all type-specific local-light properties.

Immediately before approval, Unreal checks the live Actor and component against
all frozen identity fields and the complete original snapshot. An intervening
rename, replacement, unit change, property edit, lock, or deletion invalidates
the proposal. The operator must prepare a new one.

Approved properties are applied inside `FScopedTransaction` through an Editor
property-edit path that supports Static, Stationary, and Movable lights. Normal
post-edit and render-state notifications run afterward. The level is marked
dirty but never saved automatically; Ctrl+Z restores the previous properties.

## UI workflow

1. Select exactly one supported Light Actor.
2. Enter a request such as `Make this Spot Light 20% brighter, set it to 3200 K,
   widen the outer cone to 45 degrees, and rotate it right by 30 degrees.`
3. Press **Prepare Light**.
4. Review the exact target, frozen unit, changed properties, and complete
   Before/After state.
5. Press **Approve & Apply Light**, or **Reject**.
6. Use Ctrl+Z if the approved result should be reverted.

Spawn/delete/rename/retype requests, multi-light coordination, and unsupported
properties resolve as **Unresolved**. The assistant does not silently substitute
a different operation.

## CLI and contracts

The proposal boundary can be exercised without mutating Unreal:

```powershell
render-master assistant-light-propose `
  --prompt "Make this light 20% brighter and set it to 3200 K" `
  --context examples/unreal_light_context.json `
  --proposal-id light_example_001 `
  --output light_proposal.json
```

Export or validate the public boundary:

```powershell
render-master schema unreal-light-context
render-master schema assistant-light-proposal
render-master validate examples/unreal_light_context.json `
  --contract unreal-light-context
```

Local request, context, proposal, raw model output, and process logs are written
below the configured workflow root under `assistant-light/<proposal-id>/`; they
remain outside Git.

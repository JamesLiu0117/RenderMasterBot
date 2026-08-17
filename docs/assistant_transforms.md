# Approval-gated Actor Transform actions

The Unreal Assistant can prepare and apply one bounded world-space Transform
change for exactly one selected Actor. Proposal generation is read-only. The
level changes only after the operator reviews a dedicated **Transform Action**
card and presses **Approve & Apply Transform**.

## Why the model does not own the final Transform

The local planner receives Editor-captured evidence but can return only a
`TransformEditIntent`. Each Transform channel is restricted to a small operation
set:

| Channel | Allowed operations | Units |
| --- | --- | --- |
| Location | preserve, set, add | centimeters |
| Rotation | preserve, set, add | degrees |
| Scale | preserve, set, multiply | unitless |

Omitted axes remain unchanged. Rotation `x`, `y`, and `z` map to Unreal Roll,
Pitch, and Yaw. Version 0.1 supports world space only.

Python, not the model, applies those operations to the trusted Before values. It
rejects non-finite values, unsupported operations, excessive location or
rotation edits, unsafe scale magnitudes, and no-op results. The resulting
`AssistantTransformProposal` therefore contains host-computed After values and
an auditable list of changed axes.

## Approval boundary

Before planning, the Editor freezes:

- level and Actor name;
- Actor path, class, and GUID;
- root-component name and mobility;
- editability and location-lock state;
- world location, rotation, and scale.

Immediately before applying an approved proposal, the Editor checks the live
Actor against all frozen identity fields and the original Transform. If the
Actor was renamed, replaced, locked, deleted, or moved while the proposal was
open, application fails and the operator must prepare a new proposal.

Successful approval calls `SetActorTransform` inside `FScopedTransaction`, runs
the normal post-edit notification, marks the level dirty, and redraws the
viewport. It does not save the level. Ctrl+Z restores the previous complete
Transform.

## UI workflow

1. Select exactly one Actor.
2. Enter a request such as `Move this Actor up by 50 cm and rotate it right by
   30 degrees.`
3. Press **Prepare Transform**.
4. Review the target, requested channels, and full Before/After values.
5. Press **Approve & Apply Transform**, or **Reject**.
6. Use Ctrl+Z if the approved result should be reverted.

Requests for local-space movement, geometry-aware placement such as “exactly on
top of the table,” multi-Actor coordination, or camera properties are reported
as **Unresolved** in this milestone. Light properties use the separate
type-specific **Prepare Light** action. The assistant does not silently
approximate unsupported Transform requests.

## CLI and contracts

The same read-only proposal boundary can be exercised without Unreal mutation:

```powershell
render-master assistant-transform-propose `
  --prompt "Move this object up by 50 cm" `
  --context examples/unreal_actor_transform_context.json `
  --proposal-id transform_example_001 `
  --output transform_proposal.json
```

Export or validate the public boundary:

```powershell
render-master schema unreal-actor-transform-context
render-master schema assistant-transform-proposal
render-master validate examples/unreal_actor_transform_context.json `
  --contract unreal-actor-transform-context
```

Local request, context, proposal, and process logs are written below the
configured workflow root under `assistant-transform/<proposal-id>/`; they remain
outside Git.

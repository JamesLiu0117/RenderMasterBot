# Approval-gated Actor Transform actions

The Unreal Assistant can prepare and apply one bounded Transform action to an
ordered selection of one to 32 Actors. It supports world space and explicit
local space. Proposal generation is read-only: the level changes only after the
operator reviews the complete **Transform Action** card and presses **Approve &
Apply Transform**.

## Why the model does not own the final Transform

The Editor freezes the selected Actors and their current world Transforms. The
local planner can return only one `TransformEditIntent`, which is applied
uniformly to the complete selection by deterministic Python code.

| Channel | World-space operations | Local-space operations | Units |
| --- | --- | --- | --- |
| Location | preserve, set, add | preserve, add | centimeters |
| Rotation | preserve, set, add | preserve, add | degrees |
| Scale | preserve, set, multiply | preserve, set, multiply | unitless |

Omitted axes remain unchanged. Rotation `x`, `y`, and `z` map to Unreal Roll,
Pitch, and Yaw. A local translation is rotated by each Actor's own captured
orientation before it becomes world-space After evidence. A local rotation is
composed with each Actor's orientation as a quaternion; it is not approximated
by adding world Euler angles. Local location/rotation `set` is rejected because
an absolute value in local space is ambiguous.

Python, not the model, computes every final Transform. It rejects non-finite
values, unsupported operations, excessive location changes, unsafe scale
magnitudes, and an action that changes no selected Actor. An individual Actor
that already satisfies the request remains in the proposal as an explicit
no-op, so the review card still accounts for the complete selection.

The public `AssistantTransformBatchProposal` contains ordered Actor actions,
host-computed world-space Before/After snapshots, and exact changed-channel and
axis evidence. The earlier single-Actor contracts and CLI remain available for
backward compatibility.

## Approval boundary

Before planning, the Editor sorts the selection by Actor path and freezes, for
every Actor:

- project, level, Actor label, path, class, and GUID;
- root-component name and mobility;
- editability and location-lock state;
- world location, rotation, and scale.

Immediately before approval, Unreal rechecks every live Actor against all
frozen identity, component, mobility, and Transform evidence. If even one Actor
was renamed, replaced, locked, deleted, moved, rotated, scaled, or structurally
changed while the proposal was open, the complete batch is rejected and
nothing is applied.

Successful approval applies all changed Actors inside one `FScopedTransaction`,
runs normal post-edit notifications, marks changed packages dirty, and redraws
the viewport. A failure while applying restores prior Actors and cancels the
transaction. The level is never saved automatically, and one Ctrl+Z restores
the complete batch.

## UI workflow

1. Select between one and 32 Actors in the current level.
2. Enter one request, for example:
   - world: `Move all selected Actors up by 50 cm.`
   - local: `Move all selected Actors forward 100 cm in each Actor's local space.`
3. Press **Prepare Transform**. No scene mutation occurs.
4. Review the coordinate space and every Actor's changed channels and complete
   world-space Before/After values.
5. Press **Approve & Apply Transform**, or **Reject**.
6. Use Ctrl+Z once if the complete approved batch should be reverted.

The action intentionally applies one uniform intent to the selection. Requests
that require geometry queries, collision-aware placement, Actor-to-Actor
relationships such as “arrange these in a circle,” or specialized Light/Camera
properties resolve as **Unresolved** instead of being approximated. Light and
camera properties use their separate type-specific actions.

## CLI and contracts

Exercise the batch proposal boundary without mutating Unreal:

```powershell
render-master assistant-transform-batch-propose `
  --prompt "Move all selected Actors forward 100 cm in each Actor's local space" `
  --context examples/unreal_transform_selection_context.json `
  --proposal-id transform_batch_example_001 `
  --output transform_batch_proposal.json
```

Export or validate the public boundary:

```powershell
render-master schema unreal-transform-selection-context
render-master schema assistant-transform-batch-proposal
render-master validate examples/unreal_transform_selection_context.json `
  --contract unreal-transform-selection-context
```

The Editor writes request, selection context, proposal, and process logs below
the configured workflow root at
`assistant-transform-batch/<proposal-id>/`. These local artifacts remain outside
Git.

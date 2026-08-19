# Approval-gated three-point lighting rigs

The Unreal Assistant can prepare and apply one camera-relative Key/Fill/Rim
lighting rig around one selected subject. Proposal generation is read-only. The
level changes only after the operator reviews all three role assignments and
complete Before/After evidence in the **Lighting Rig Action** card, then presses
**Approve & Apply Rig**.

This is the first connected graphics-assistant action that coordinates actors
with different responsibilities. It is intentionally narrower than automatic
lighting design: the first version reuses exactly three existing local lights
and does not create, delete, rename, or retype Actors.

## Required selection

Select exactly five Actors in the current level:

1. one non-light, non-camera subject with observable world bounds;
2. one perspective `CameraActor` or `CineCameraActor` facing the subject;
3. exactly three Point, Spot, or Rect Lights.

Every selected Actor must be editable and unlocked. The three lights must be
Movable, use one shared non-EV intensity unit, and contain at least one positive
captured intensity. Directional Lights, Static or Stationary local lights,
orthographic cameras, mixed intensity units, EV units, and duplicate Actors are
rejected before the model is called.

Selection order does not assign lighting roles. The Editor sorts and freezes
the three light identities. The local planning model assigns each exact Actor
path to Key, Fill, or Rim exactly once, using the prompt and captured context.

## Bounded creative controls

The model chooses only the following semantic controls:

| Control | Allowed values | Meaning |
| --- | --- | --- |
| Contrast | soft, balanced, dramatic | deterministic Key/Fill/Rim intensity ratios |
| Palette | preserve, neutral, warm/cool, cool/warm | preserve temperature or apply bounded role temperatures |
| Key side | camera left, camera right | place Key relative to the captured camera view |
| Spacing | tight, standard, wide | scale camera-relative offsets from subject bounds |
| Brightness | dim, balanced, bright | scale the captured lighting baseline |

Python, not the model, computes the final world positions, aim rotations,
intensities, attenuation radii, and any required Spot Light cone coverage. The
subject bounds determine rig scale. The camera basis determines left, right,
front, and back. Spot and Rect Lights aim at the subject center; Point Light
rotation remains unchanged because it has no visual meaning.

The current deterministic contrast ratios are:

| Contrast | Key | Fill | Rim |
| --- | ---: | ---: | ---: |
| soft | 1.00 | 0.65 | 0.55 |
| balanced | 1.00 | 0.40 | 0.65 |
| dramatic | 1.00 | 0.20 | 0.90 |

Brightness multiplies the captured positive baseline by 0.75, 1.00, or 1.50.
Spacing uses 2.2, 3.0, or 4.0 times the subject bounding-sphere radius. These
rules make identical approved context and intent reproducible even if the
planning model changes later.

## AI and host responsibilities

The model interprets artistic language and returns a strict
`LightingRigIntent`: exact role assignment plus contrast, palette, key side,
spacing, and brightness. It cannot author trusted Actor identity, subject
bounds, camera Transform, light Before evidence, or final numeric values.

The host validates every returned Actor path, rejects missing or duplicate
roles, checks that the camera faces the subject, compiles deterministic numeric
results, derives the exact changed-property lists, and emits the public
`AssistantLightingRigProposal`. Invalid structured output receives at most one
bounded retry. An unsupported request resolves as **Unresolved** instead of
falling back to an unreviewed approximation.

## Approval and stale-state boundary

Before planning, the Editor freezes all five Actors:

- project and level identity;
- subject Actor identity, root component, Transform, editability, lock state,
  and world bounds;
- camera Actor/component identity, type, mobility, projection mode, Transform,
  editability, and lock state;
- each light Actor/component identity, type, mobility, intensity unit,
  editability, lock state, location, and complete supported property snapshot.

Immediately before approval, Unreal captures the same evidence again. A moved
subject or camera, changed bounds, replaced component, renamed or edited light,
unit/type/Mobility change, lock, or deletion invalidates the complete proposal.
Nothing is partially applied.

Successful approval edits all three lights inside one `FScopedTransaction`.
Any mid-apply failure restores earlier changes and cancels the transaction.
Normal post-edit and render-state notifications run afterward. The subject and
camera are evidence only and are never changed. The level is marked dirty but
never saved automatically; one Ctrl+Z restores the complete lighting rig.

## Camera-view visual review

After the first rig is applied, **Evaluate Applied Rig** closes one bounded
feedback loop. It temporarily moves the active perspective Level Editor
viewport to the frozen camera, forces Lit Game View for one PNG, and restores
the original viewport location, rotation, FOV, view mode, and Game View state.
The exact PNG is hashed and reviewed by the configured local vision model.

The model classifies only overall exposure, Fill strength, and Rim separation.
Python converts those categories to fixed intensity factors; it does not accept
numeric values from the model. A proposed correction is displayed separately
and requires approval. Unreal revalidates the subject, camera, and all three
lights before applying only the three intensities in a second transaction.
One Ctrl+Z removes the correction; a second Ctrl+Z removes the original rig.

## UI workflow

1. Select one subject, one perspective Camera or Cine Camera, and exactly three
   compatible Movable local lights.
2. Enter an artistic request, for example: `Create a warm cinematic
   three-point lighting rig around this subject, with the key on camera left
   and dramatic contrast.`
3. Press **Prepare Lighting Rig**. No Editor scene mutation occurs.
4. Review the subject, camera, chosen style, Key/Fill/Rim Actor assignments,
   changed properties, and complete per-light Before/After values.
5. Press **Approve & Apply Rig**, or **Reject** to make no change.
6. Optionally press **Evaluate Applied Rig** with a perspective Level Editor
   viewport active. The viewport is restored immediately after one Lit PNG.
7. If the review passes, no action is created. If it proposes a correction,
   review the exact intensity Before/After values and approve or reject it.
8. One Ctrl+Z removes an approved correction; a second Ctrl+Z restores the
   original three lights. Save the level manually only if the result should
   persist.

## Current limits

The first version does not spawn missing lights, use Directional Lights,
convert intensity units, change light tint or shadow casting, modify the
subject or camera, move lights during visual refinement, coordinate multiple
cameras, or choose different rigs per shot. The review uses the active Level
Editor viewport rather than Movie Render Queue and is limited to one pass plus
one optional intensity correction. Unsupported requests resolve as
**Unresolved** or fail the deterministic compatibility check.

The rig is therefore a safe, reviewable starting composition rather than a
claim of final art direction. After approval, the existing **Prepare Light**
action can make a compatible group adjustment, or individual lights can be
selected and refined through normal Unreal controls.

## CLI and contracts

Exercise the read-only proposal boundary without launching Unreal:

```powershell
render-master assistant-lighting-rig-propose `
  --prompt "Create a warm cinematic three-point rig with dramatic contrast" `
  --context examples/unreal_lighting_rig_context.json `
  --proposal-id lighting_rig_example_001 `
  --output lighting_rig_proposal.json
```

Export or validate the two public contracts:

```powershell
render-master schema unreal-lighting-rig-context
render-master schema assistant-lighting-rig-proposal
render-master schema unreal-lighting-rig-review-context
render-master schema assistant-lighting-rig-review-proposal
render-master validate examples/unreal_lighting_rig_context.json `
  --contract unreal-lighting-rig-context
```

The Editor writes the request, frozen context, proposal, and process logs below
the configured workflow root at `assistant-lighting-rig/<proposal-id>/`. These
local artifacts remain outside Git.

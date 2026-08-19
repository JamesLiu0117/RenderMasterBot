# Camera-view lighting-rig review

After an approved Key/Fill/Rim rig is applied, the Unreal Assistant can review
one image from the frozen rig camera and prepare at most one small
intensity-only correction. This closes a visible feedback loop without giving
the vision model authority to move Actors or write arbitrary light values.

## What happens

1. **Evaluate Applied Rig** verifies that the applied subject, camera, and three
   lights still match the approved rig.
2. The active perspective Level Editor viewport temporarily adopts the frozen
   camera location, rotation, and FOV, switches to Lit Game View, and captures
   one PNG. Its previous location, rotation, FOV, view mode, and Game View state
   are restored immediately.
3. The host computes the PNG SHA-256 and deterministic luminance statistics.
   Empty, oversized, non-PNG, blank-like, underexposed-like, and
   overexposed-like evidence has explicit validation rules.
4. `qwen3-vl:8b-instruct` receives the exact PNG, source lighting request,
   frozen rig context, and image statistics. It returns only three categories:
   overall exposure, Fill balance, and Rim separation.
5. A balanced result passes without creating an action. An unreadable or
   unsupported result becomes **Unresolved**. Otherwise, the host computes a
   deterministic proposal for all three existing light intensities.
6. The **Lighting Rig Visual Review** card shows the diagnosis and exact
   Before/After values. The level remains unchanged until **Approve Intensity
   Correction** is pressed.
7. Approval revalidates the subject, camera, and three lights. One stale Actor
   rejects the entire correction. A successful correction is one new Unreal
   transaction and never saves the level automatically.

## Bounded correction rules

The vision model never returns numbers. Overall exposure uses a factor of 1.2
for `too_dark`, 1.0 for `balanced`, or `1 / 1.2` for `too_bright`. Fill and Rim
use the same factors for `too_weak`, `balanced`, and `too_strong`. The global
factor applies to all three lights; the role factor applies only to Fill or Rim.
The largest combined increase is therefore 1.44.

The correction can change only intensity. It preserves light location,
rotation, intensity unit, color, temperature toggle and value, shadow casting,
attenuation radius, cone angles, Actor identity, role order, camera, and
subject. The first Ctrl+Z removes the visual correction; the second Ctrl+Z
removes the original three-point rig.

## Deliberate limits

- One successful visual review and one optional correction per applied rig.
- One bounded structured-output retry if the local model returns invalid or
  contradictory categories.
- A complete but internally contradictory diagnosis after that retry is stored
  as **Unresolved** with no action; malformed or incomplete output still fails.
- A perspective Level Editor viewport must exist; headless `-nullrhi` testing
  can validate parsing and application but cannot prove the visible capture.
- This version uses the Level Editor viewport, not Movie Render Queue or final
  cinematic render settings.
- Requests requiring light movement, color, temperature, attenuation, cones,
  shadows, material changes, camera changes, or subject changes are unresolved.

## CLI and local evidence

The Editor runs the equivalent of:

```powershell
render-master assistant-lighting-rig-review `
  --prompt-file review_request.txt `
  --context lighting_rig_review_context.json `
  --preview lighting_rig_preview.png `
  --model qwen3-vl:8b-instruct `
  --proposal-id lighting_review_001 `
  --raw-output lighting_rig_review_raw.json `
  --output lighting_rig_review_proposal.json
```

Request, context, PNG, proposal, and process logs stay below the configured
workflow root at `assistant-lighting-rig-review/<review-id>/`, outside Git. The
public contracts are `unreal-lighting-rig-review-context` and
`assistant-lighting-rig-review-proposal`.

The repository example can be validated independently:

```powershell
render-master validate examples/unreal_lighting_rig_review_context.json `
  --contract unreal-lighting-rig-review-context
```

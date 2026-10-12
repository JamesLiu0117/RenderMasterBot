# Conversational Assistant V1

## Product role

The conversation layer is the primary user interaction surface for
RenderMasterBot. It is not a new unrestricted executor. It coordinates the
existing independently validated material, Transform, light, camera, render,
audit, profiling, optimization, verification, and visual-review capabilities.

```text
user message + recent history + live Unreal context
                         |
                         v
              local conversation router
                /         |          \
               /          |           \
          respond       clarify       route
                                         |
                                         v
                            registered specialized tool
                                         |
                                         v
                         proposal / evidence / review
                                         |
                                         v
                         explicit approval + revalidation
                                         |
                                         v
                              Undo-backed Unreal edit
```

## Public contracts

- `UnrealConversationContext` contains the current project, level, Editor or
  PIE/SIE mode, at most 32 ordered selected Actors, a truncation marker, and
  bounded readiness flags for dependent workflows. It also names a pending
  Action Card so follow-up questions remain available during review.
- `AssistantConversationRequest` contains one current request and at most 24
  previous user/assistant messages. The current message is not duplicated in
  history.
- `AssistantConversationTurn` binds the response to the exact conversation ID,
  turn ID, request, canonical context hash, model identity, and attempt count.

The turn outcome is exactly one of:

1. `respond`: answer without an executable action.
2. `clarify`: ask one question and name the missing context.
3. `route`: choose one registered action intent and provide the bounded request
   passed to that specialized capability.

## Host-enforced boundaries

Python validates the local model response and retries one malformed response.
It then checks context prerequisites independently of the model. For example,
a Transform route without a selected Actor becomes a clarification; profiling
without active PIE/SIE becomes a clarification; shadow optimization without a
completed stable profile becomes a clarification.

A stable pending proposal does not disable conversation. The Assistant may
answer a question about the review state, but a second action route becomes a
clarification requiring the existing Action Card to be approved or rejected.
Long-running planning, capture, trace, render, and import processes still block
another model turn so concurrent workflows cannot race each other.

C++ parses the final turn again. It accepts only the fixed outcome and intent
vocabulary, rechecks the active conversation ID, turn ID, and original request,
and maps a route through an explicit `if` chain to an existing host capability.
There is no dynamic command name, reflection call, script body, or arbitrary
Unreal API dispatch.

For a first-turn action, the specialized tool receives the exact user request.
The general conversation model cannot convert units or invent an engine axis.
A later multi-turn request may be made self-contained from conversation history,
but the specialized tool still validates semantics and produces reviewable
Before/After evidence.

## Editor experience

- **Conversation** shows the persistent local timeline.
- **Current Activity** shows live planning, capture, proposal, and workflow
  state from the active specialized capability.
- **Send to Assistant** is the normal entry point.
- **New Conversation** starts a new local conversation; archived request artifacts
  are retained.
- **Advanced Direct Tools** preserves the previous explicit capability buttons
  in a collapsed expert/debug area.
- Existing proposal cards remain visible and keep their explicit approval,
  rejection, stale-state checking, no-auto-save, and Undo behavior.

## Latency, provenance, and result delivery

Common capability questions and exact profiling commands have a small,
auditable host route (`host-rules:conversation-v1`). Matching uses the whole
utterance, not keyword substrings: negations, quotations, and added constraints
go through the local model. Both paths still enforce PIE/SIE prerequisites and
pending-review boundaries. This shortcut is not a trained-model improvement.

Free-form routing uses a 1,024-token generation budget. GPT-OSS defaults to
`think: low`; explicit caller settings take precedence. Structured chat requests
for Qwen 3.5 default to `think: false` to bound interactive reasoning overhead.
Other model families retain their server default. Requests
default to `keep_alive: 0`, releasing the model after inference rather than
deliberately keeping its GPU allocation during subsequent Unreal captures.
This trades warm-model reuse for reduced residency interference; it does not
prove the absence of other GPU workloads.

Every CLI conversation turn writes a sibling `.metrics.json` with routing
source, elapsed routing time, model identity, and available token counts.
Routing time excludes Editor polling and Python process startup.

Profiling completion, partial completion, failure, and cancellation now return
to the conversation timeline. Successful evidence events bind the report path
and file hash; a missing report cannot be presented as a successful result.

## Current limits

- The local session snapshot persists the timeline. Transform task recovery is
  supported; an interrupted profiling capture is not resumable from a saved
  conversation alone. Individual request, turn, and evidence artifacts remain
  in the configured workflow root.
- The router receives bounded live context, not the complete asset catalog,
  source tree, or profiler history. Detailed questions that need those sources
  must route to the appropriate evidence-producing tool.
- Only the most recent 24 messages are sent to the local model. The visible
  timeline retains up to 100 messages.
- Existing action cards remain beside the conversation. Completion events are
  connected for Transform and profiling, not yet every specialized workflow.

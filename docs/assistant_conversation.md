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
- **New Conversation** resets only in-memory conversation state.
- **Advanced Direct Tools** preserves the previous explicit capability buttons
  in a collapsed expert/debug area.
- Existing proposal cards remain visible and keep their explicit approval,
  rejection, stale-state checking, no-auto-save, and Undo behavior.

## Current limits

- The V1 timeline is in-memory for the lifetime of the Editor panel; individual
  request and turn JSON artifacts remain in the configured workflow root.
- The router receives bounded live context, not the complete asset catalog,
  source tree, or profiler history. Detailed questions that need those sources
  must route to the appropriate evidence-producing tool.
- Only the most recent 24 messages are sent to the local model. The visible
  timeline retains up to 100 messages.
- Rich inline action cards and explicit tool-result messages in the timeline are
  the next UI refinement; existing action cards currently remain beside the
  conversation.

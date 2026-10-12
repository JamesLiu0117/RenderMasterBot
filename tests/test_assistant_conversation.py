import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from render_master_bot.assistant_conversation import (
    ConversationRoutingError,
    load_conversation_request,
    route_conversation_turn,
)
from render_master_bot.cli import build_parser, cmd_assistant_conversation_turn
from render_master_bot.contracts import (
    AssistantConversationRequest,
    AssistantConversationSession,
    AssistantToolResultEvent,
    ConversationRouteIntent,
    UnrealConversationContext,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import contract_schema
from render_master_bot.conversation_rules import PROFILING_PROMPT


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(
            content=next(self.contents),
            model="fake-conversation-model",
        )


def unreal_context(*selection, play_mode="editor", **updates):
    value = {
        "project_name": "OptimizationPlugin",
        "level_path": "/Game/Maps/PerformanceTest",
        "play_mode": play_mode,
        "selection": list(selection),
        "selection_truncated": False,
        "lighting_rig_review_ready": False,
        "shadow_optimization_ready": False,
        "shadow_verification_ready": False,
        "pending_action_intent": None,
    }
    value.update(updates)
    return UnrealConversationContext.model_validate(value)


def selected_actor(name="RockA", kind="static_mesh"):
    return {
        "actor_name": name,
        "actor_path": f"/Game/Maps/PerformanceTest:PersistentLevel.{name}",
        "actor_class": "StaticMeshActor" if kind == "static_mesh" else "Actor",
        "actor_kind": kind,
    }


def tool_result_event(**updates):
    value = {
        "event_id": "result_001",
        "conversation_id": "conversation_001",
        "source_turn_id": "turn_000",
        "intent": "transform",
        "phase": "proposal",
        "status": "needs_review",
        "summary": "Prepared a Transform proposal for RockA; it has not been applied.",
        "proposal_id": "transform_001",
        "report_id": None,
        "subject_ids": [
            "/Game/Maps/PerformanceTest:PersistentLevel.RockA",
        ],
        "evidence": [{
            "evidence_id": "proposal_file_001",
            "kind": "proposal",
            "path": "C:/Runtime/transform_001.json",
            "sha256": "a" * 64,
        }],
        "created_at_utc": datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
    }
    value.update(updates)
    return AssistantToolResultEvent.model_validate(value)


def request(context=None, history=None, result_events=None):
    return AssistantConversationRequest(
        conversation_id="conversation_001",
        turn_id="turn_001",
        request="Move the selected rock 100 cm upward.",
        context=context or unreal_context(selected_actor()),
        history=history or [],
        result_events=result_events or [],
    )


def decision(**updates):
    value = {
        "schema_version": "0.1",
        "outcome": "route",
        "intent": "transform",
        "assistant_message": (
            "I can prepare that Transform change for the selected Actor."
        ),
        "action_prompt": "Move the complete selected Actor set upward by 100 cm in world space.",
        "clarification_question": None,
        "required_context": [],
    }
    value.update(updates)
    return json.dumps(value)


class AssistantConversationTests(unittest.TestCase):
    def test_explicit_profile_preflight_does_not_call_model(self):
        current = request().model_copy(update={"request": PROFILING_PROMPT})
        client = FakeClient([])
        result = route_conversation_turn(request=current, client=client, model="planner")
        self.assertEqual(result.turn.outcome, "clarify")
        self.assertEqual(result.turn.required_context, ["active_pie_or_sie"])
        self.assertEqual(result.turn.model, "host-rules:conversation-v1")
        self.assertFalse(client.requests)

    def test_explicit_profile_preserves_constraints_and_pending_review_gate(self):
        for mode in ("pie", "sie"):
            with self.subTest(mode=mode):
                current = request(context=unreal_context(play_mode=mode)).model_copy(
                    update={"request": PROFILING_PROMPT}
                )
                result = route_conversation_turn(request=current, client=FakeClient([]), model="planner")
                self.assertEqual(result.turn.intent, "level_profiling")
                self.assertEqual(result.turn.action_prompt, PROFILING_PROMPT)
                pending = current.model_copy(update={"context": unreal_context(
                    play_mode=mode, pending_action_intent="transform",
                )})
                result = route_conversation_turn(request=pending, client=FakeClient([]), model="planner")
                self.assertEqual(result.turn.required_context, ["pending_action_decision"])

    def test_capability_question_has_explicit_host_provenance(self):
        for text in ("你能做什么？", "What can you do?"):
            current = request().model_copy(update={"request": text})
            result = route_conversation_turn(request=current, client=FakeClient([]), model="planner")
            self.assertEqual(result.turn.outcome, "respond")
            self.assertEqual(result.turn.model, "host-rules:conversation-v1")

    def test_negated_quoted_and_extended_requests_are_not_keyword_routed(self):
        for text in (
            "Do not profile the current level", 'Explain "profile the current level"',
            "Can you profile the current level?", "Profile the current level and delete actors",
        ):
            with self.subTest(text=text):
                client = FakeClient(decision(outcome="respond", intent="answer_question", action_prompt=None))
                route_conversation_turn(request=request().model_copy(update={"request": text}), client=client, model="planner")
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(client.requests[0]["num_predict"], 1024)

    def test_route_preserves_ids_and_emits_only_registered_intent(self):
        client = FakeClient(decision())
        result = route_conversation_turn(
            request=request(),
            client=client,
            model="planner",
        )

        self.assertEqual(result.turn.outcome, "route")
        self.assertEqual(result.turn.intent, "transform")
        self.assertEqual(result.turn.conversation_id, "conversation_001")
        self.assertEqual(result.turn.turn_id, "turn_001")
        self.assertEqual(result.turn.attempt_count, 1)
        self.assertEqual(result.turn.action_prompt, request().request)
        self.assertEqual(len(result.turn.context_sha256), 64)
        self.assertFalse(result.turn.required_context)
        self.assertNotIn("think", client.requests[0])

    def test_recent_history_is_sent_before_current_host_context(self):
        current = request(history=[
            {
                "message_id": "message_001",
                "role": "user",
                "content": "Can you help with this level?",
            },
            {
                "message_id": "message_002",
                "role": "assistant",
                "content": "Yes. Select a target or ask for a whole-level analysis.",
            },
        ])
        client = FakeClient(decision())
        route_conversation_turn(request=current, client=client, model="planner")

        messages = client.requests[0]["messages"]
        self.assertEqual([item["role"] for item in messages], [
            "system",
            "user",
            "assistant",
            "user",
        ])
        self.assertIn("current_unreal_context", messages[-1]["content"])

    def test_host_tool_results_are_grounded_in_current_turn_payload(self):
        current = request(result_events=[tool_result_event()])
        client = FakeClient(decision(
            outcome="respond",
            intent="answer_question",
            assistant_message=(
                "The Transform proposal is ready for review and has not been applied."
            ),
            action_prompt=None,
        ))
        route_conversation_turn(request=current, client=client, model="planner")

        prompt = client.requests[0]["messages"][-1]["content"]
        payload = json.loads(prompt.split("instructions:\n", maxsplit=1)[1])
        self.assertEqual(
            payload["recent_tool_results"][0]["status"],
            "needs_review",
        )
        self.assertEqual(
            payload["recent_tool_results"][0]["proposal_id"],
            "transform_001",
        )
        self.assertIn("host-authored records", client.requests[0]["messages"][0]["content"])

    def test_tool_result_event_requires_phase_status_consistency(self):
        with self.assertRaisesRegex(ValidationError, "cannot use status"):
            tool_result_event(phase="proposal", status="applied")

    def test_tool_result_event_requires_proposal_identity_for_review(self):
        with self.assertRaisesRegex(ValidationError, "requires proposal_id"):
            tool_result_event(proposal_id=None)

    def test_tool_result_event_requires_explicit_utc_timestamp(self):
        with self.assertRaisesRegex(ValidationError, "explicit UTC timezone"):
            tool_result_event(created_at_utc=datetime(2026, 9, 5, 12, 0))

    def test_request_rejects_tool_results_from_another_conversation(self):
        with self.assertRaisesRegex(ValidationError, "match the request conversation ID"):
            request(result_events=[tool_result_event(conversation_id="conversation_999")])

    def test_request_rejects_duplicate_tool_result_events(self):
        event = tool_result_event()
        with self.assertRaisesRegex(ValidationError, "event IDs must be unique"):
            request(result_events=[event, event])

    def test_session_binds_reviewable_task_to_exact_proposal_evidence(self):
        event = tool_result_event()
        session = AssistantConversationSession(
            conversation_id="conversation_001",
            project_name="OptimizationPlugin",
            history=[],
            result_events=[event],
            active_task={
                "source_turn_id": "turn_000",
                "intent": "transform",
                "status": "needs_review",
                "proposal_id": "transform_001",
                "proposal_evidence": event.evidence[0],
            },
            updated_at_utc=datetime(2026, 9, 5, 12, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(session.active_task.proposal_id, "transform_001")

    def test_session_rejects_review_task_without_matching_result(self):
        event = tool_result_event()
        with self.assertRaisesRegex(ValidationError, "proposal result event"):
            AssistantConversationSession(
                conversation_id="conversation_001",
                project_name="OptimizationPlugin",
                result_events=[],
                active_task={
                    "source_turn_id": "turn_000",
                    "intent": "transform",
                    "status": "needs_review",
                    "proposal_id": "transform_001",
                    "proposal_evidence": event.evidence[0],
                },
                updated_at_utc=datetime(
                    2026, 9, 5, 12, 1, tzinfo=timezone.utc
                ),
            )

    def test_session_rejects_changed_review_evidence(self):
        event = tool_result_event()
        changed = event.evidence[0].model_copy(update={"sha256": "b" * 64})
        with self.assertRaisesRegex(ValidationError, "evidence must match"):
            AssistantConversationSession(
                conversation_id="conversation_001",
                project_name="OptimizationPlugin",
                result_events=[event],
                active_task={
                    "source_turn_id": "turn_000",
                    "intent": "transform",
                    "status": "needs_review",
                    "proposal_id": "transform_001",
                    "proposal_evidence": changed,
                },
                updated_at_utc=datetime(
                    2026, 9, 5, 12, 1, tzinfo=timezone.utc
                ),
            )

    def test_host_converts_unsafe_missing_selection_route_to_clarification(self):
        client = FakeClient(decision())
        result = route_conversation_turn(
            request=request(context=unreal_context()),
            client=client,
            model="planner",
        )

        self.assertEqual(result.turn.outcome, "clarify")
        self.assertEqual(result.turn.intent, "clarify")
        self.assertEqual(result.turn.required_context, ["actor_selection"])
        self.assertIsNone(result.turn.action_prompt)

    def test_profiling_requires_active_play_workload(self):
        client = FakeClient(decision(
            intent="level_profiling",
            assistant_message="I will profile the complete active level workload.",
            action_prompt="Profile the complete level and identify measured bottlenecks.",
        ))
        blocked = route_conversation_turn(
            request=request(context=unreal_context()),
            client=client,
            model="planner",
        )
        self.assertEqual(blocked.turn.outcome, "clarify")
        self.assertEqual(blocked.turn.required_context, ["active_pie_or_sie"])

    def test_pending_action_blocks_a_second_route_but_not_conversation(self):
        client = FakeClient(decision())
        blocked = route_conversation_turn(
            request=request(context=unreal_context(
                selected_actor(),
                pending_action_intent="camera",
            )),
            client=client,
            model="planner",
        )
        self.assertEqual(blocked.turn.outcome, "clarify")
        self.assertEqual(
            blocked.turn.required_context,
            ["pending_action_decision"],
        )

    def test_general_question_can_respond_without_action(self):
        client = FakeClient(decision(
            outcome="respond",
            intent="answer_question",
            assistant_message=(
                "I can analyze the current level, propose bounded edits, and verify "
                "approved changes."
            ),
            action_prompt=None,
        ))
        result = route_conversation_turn(
            request=request(),
            client=client,
            model="planner",
        )
        self.assertEqual(result.turn.outcome, "respond")
        self.assertIsNone(result.turn.action_prompt)

    def test_clarification_can_preserve_recognized_action_intent(self):
        client = FakeClient(decision(
            outcome="clarify",
            intent="transform",
            assistant_message="Which Actor should I move?",
            action_prompt=None,
            clarification_question="Which Actor should I move?",
            required_context=["actor_selection"],
        ))
        result = route_conversation_turn(
            request=request(context=unreal_context()),
            client=client,
            model="planner",
        )
        self.assertEqual(result.turn.outcome, "clarify")
        self.assertEqual(result.turn.intent, "transform")
        self.assertEqual(result.turn.required_context, ["actor_selection"])

    def test_invalid_model_payload_gets_one_strict_retry(self):
        client = FakeClient(["not-json", decision()])
        result = route_conversation_turn(
            request=request(),
            client=client,
            model="planner",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertIsNotNone(result.recovery_reason)
        self.assertEqual(len(client.requests), 2)

    def test_second_invalid_payload_is_terminal(self):
        client = FakeClient(["not-json", "still-not-json"])
        with self.assertRaisesRegex(
            ConversationRoutingError,
            "after one retry",
        ):
            route_conversation_turn(
                request=request(),
                client=client,
                model="planner",
            )

    def test_contract_rejects_route_to_non_action_intent(self):
        with self.assertRaisesRegex(
            ValidationError,
            "registered action intent",
        ):
            ConversationRouteIntent.model_validate({
                "outcome": "route",
                "intent": "answer_question",
                "assistant_message": "I will answer.",
                "action_prompt": "Run an answer.",
                "clarification_question": None,
                "required_context": [],
            })

    def test_request_loader_and_public_schemas(self):
        current = request()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation_request.json"
            path.write_text(current.model_dump_json(), encoding="utf-8")
            self.assertEqual(load_conversation_request(path), current)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                ConversationRoutingError,
                "invalid assistant conversation request",
            ):
                load_conversation_request(path)

        self.assertEqual(
            contract_schema("assistant-conversation-turn")["title"],
            "AssistantConversationTurn",
        )
        self.assertEqual(
            contract_schema("assistant-tool-result-event")["title"],
            "AssistantToolResultEvent",
        )
        self.assertEqual(
            contract_schema("assistant-conversation-session")["title"],
            "AssistantConversationSession",
        )

    def test_cli_parser_exposes_conversation_boundary(self):
        args = build_parser().parse_args([
            "assistant-conversation-turn",
            "--request",
            "request.json",
            "--output",
            "turn.json",
        ])
        self.assertEqual(args.request, "request.json")
        self.assertEqual(args.output, "turn.json")

    def test_cli_writes_validated_conversation_turn(self):
        current = request()
        client = FakeClient(decision())
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            output_path = Path(directory) / "turn.json"
            request_path.write_text(current.model_dump_json(), encoding="utf-8")
            args = SimpleNamespace(
                request=str(request_path),
                model=None,
                output=str(output_path),
            )
            with (
                patch(
                    "render_master_bot.cli._settings",
                    return_value=SimpleNamespace(planner_model="planner"),
                ),
                patch("render_master_bot.cli._client", return_value=client),
            ):
                self.assertEqual(cmd_assistant_conversation_turn(args), 0)

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["intent"], "transform")
            self.assertEqual(payload["conversation_id"], "conversation_001")


if __name__ == "__main__":
    unittest.main()

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_light_rigs import load_lighting_rig_context
from render_master_bot.assistant_lighting_reviews import (
    LightingRigReviewError,
    compile_lighting_rig_review,
    load_lighting_rig_review_context,
    propose_lighting_rig_review,
)
from render_master_bot.contracts import (
    LightingRigReviewIntent,
    UnrealLightingRigReviewContext,
)
from render_master_bot.ollama import StructuredResponse


RIG_EXAMPLE = Path(__file__).parents[1] / "examples" / "unreal_lighting_rig_context.json"
REVIEW_EXAMPLE = (
    Path(__file__).parents[1]
    / "examples"
    / "unreal_lighting_rig_review_context.json"
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def make_rgba_png(width: int, height: int, pixel_at) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(pixel_at(x, y))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _png_chunk(b"IEND", b"")
    )


HEALTHY_PNG = make_rgba_png(
    64,
    64,
    lambda x, y: (180, 145, 95, 255)
    if 14 <= x < 50 and 10 <= y < 54
    else (12, 12, 16, 255),
)
BLANK_PNG = make_rgba_png(64, 64, lambda _x, _y: (64, 64, 64, 255))


class FakeVisionClient:
    def __init__(self, contents: list[str], model: str = "qwen3-vl:8b-instruct"):
        self.contents = iter(contents)
        self.model = model
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(content=next(self.contents), model=self.model)


def review_context() -> UnrealLightingRigReviewContext:
    rig = load_lighting_rig_context(RIG_EXAMPLE)
    roles = ("key", "fill", "rim")
    return UnrealLightingRigReviewContext(
        source_request="Create a warm cinematic three-point rig.",
        rig=rig,
        assignments=[
            {"actor_path": light.target.actor_path, "role": role}
            for light, role in zip(rig.lights, roles, strict=True)
        ],
    )


def intent(**overrides) -> LightingRigReviewIntent:
    value = {
        "outcome": "proposed",
        "exposure": "balanced",
        "fill_balance": "too_weak",
        "rim_separation": "balanced",
        "confidence": 0.88,
        "summary": "The subject is visible, but the shadow side needs more detail.",
        "rationale": "A small Fill increase should improve visible shadow detail.",
        "missing_capabilities": [],
    }
    value.update(overrides)
    return LightingRigReviewIntent.model_validate(value)


def intent_json(**overrides) -> str:
    return intent(**overrides).model_dump_json()


class LightingRigReviewContractTests(unittest.TestCase):
    def test_vision_intent_schema_requires_every_categorical_field(self):
        required = set(LightingRigReviewIntent.model_json_schema()["required"])
        self.assertTrue(
            {
                "outcome",
                "exposure",
                "fill_balance",
                "rim_separation",
                "confidence",
                "summary",
                "rationale",
                "missing_capabilities",
            }.issubset(required)
        )

    def test_review_context_example_validates(self):
        self.assertEqual(
            load_lighting_rig_review_context(REVIEW_EXAMPLE),
            review_context(),
        )

    def test_assignments_must_preserve_current_light_order(self):
        value = review_context().model_dump(mode="python")
        value["assignments"] = list(reversed(value["assignments"]))

        with self.assertRaisesRegex(ValidationError, "preserve current light order"):
            UnrealLightingRigReviewContext.model_validate(value)

    def test_compile_changes_only_bounded_intensity(self):
        context = review_context()
        actions = compile_lighting_rig_review(
            context,
            intent(
                exposure="too_dark",
                fill_balance="too_weak",
                rim_separation="too_strong",
            ),
        )

        self.assertAlmostEqual(
            actions[0].after.light.intensity,
            actions[0].before.light.intensity * 1.2,
        )
        self.assertAlmostEqual(
            actions[1].after.light.intensity,
            actions[1].before.light.intensity * 1.44,
        )
        self.assertAlmostEqual(
            actions[2].after.light.intensity,
            actions[2].before.light.intensity,
        )
        self.assertTrue(all(action.before.location_cm == action.after.location_cm for action in actions))
        self.assertTrue(
            all(
                [change.property for change in action.changes] in ([], ["intensity"])
                for action in actions
            )
        )

    def test_proposal_binds_image_statistics_and_model_identity(self):
        client = FakeVisionClient([intent_json()])
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            image_path.write_bytes(HEALTHY_PNG)
            result = propose_lighting_rig_review(
                request="Review and improve this applied lighting rig.",
                context=review_context(),
                preview_path=image_path,
                client=client,
                model="qwen3-vl:8b-instruct",
                proposal_id="lighting_review_001",
            )

        self.assertEqual(result.proposal.status, "proposed")
        self.assertEqual(len(result.proposal.actions), 3)
        self.assertEqual(result.proposal.proposed_by.model, "qwen3-vl:8b-instruct")
        self.assertEqual(len(result.proposal.preview.sha256), 64)
        self.assertFalse(result.proposal.preview.blank_like)
        self.assertFalse(client.requests[0]["think"])
        self.assertEqual(len(client.requests[0]["messages"][1]["images"]), 1)

    def test_balanced_healthy_preview_can_pass_without_actions(self):
        client = FakeVisionClient([
            intent_json(
                outcome="pass",
                fill_balance="balanced",
                summary="The applied rig is balanced and satisfies the requested look.",
                rationale="Exposure, shadow detail, and rim separation are visibly balanced.",
            )
        ])
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            image_path.write_bytes(HEALTHY_PNG)
            result = propose_lighting_rig_review(
                request="Review the applied rig.",
                context=review_context(),
                preview_path=image_path,
                client=client,
                model="qwen3-vl:8b-instruct",
                proposal_id="lighting_review_pass",
            )

        self.assertEqual(result.proposal.status, "pass")
        self.assertEqual(result.proposal.actions, [])

    def test_blank_preview_forces_bounded_retry_to_unresolved(self):
        client = FakeVisionClient([
            intent_json(
                outcome="pass",
                fill_balance="balanced",
                summary="The rig appears balanced.",
                rationale="No change is needed.",
            ),
            intent_json(
                outcome="unresolved",
                fill_balance="balanced",
                summary="The image contains no usable subject evidence.",
                rationale="A blank-like frame cannot support lighting judgment.",
                missing_capabilities=["A non-blank camera preview is required."],
            ),
        ])
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            image_path.write_bytes(BLANK_PNG)
            result = propose_lighting_rig_review(
                request="Review the applied rig.",
                context=review_context(),
                preview_path=image_path,
                client=client,
                model="qwen3-vl:8b-instruct",
                proposal_id="lighting_review_blank",
            )

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.proposal.status, "unresolved")
        self.assertTrue(result.proposal.preview.blank_like)

    def test_non_png_is_rejected_before_model_call(self):
        client = FakeVisionClient([intent_json()])
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            image_path.write_bytes(b"not a png")
            with self.assertRaisesRegex(LightingRigReviewError, "not a PNG"):
                propose_lighting_rig_review(
                    request="Review the applied rig.",
                    context=review_context(),
                    preview_path=image_path,
                    client=client,
                    model="qwen3-vl:8b-instruct",
                    proposal_id="lighting_review_invalid",
                )
        self.assertEqual(client.requests, [])

    def test_unsafe_review_fails_after_one_retry(self):
        invalid = json.dumps({"outcome": "pass"})
        client = FakeVisionClient([invalid, invalid])
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            image_path.write_bytes(HEALTHY_PNG)
            with self.assertRaisesRegex(LightingRigReviewError, "after one retry"):
                propose_lighting_rig_review(
                    request="Review the applied rig.",
                    context=review_context(),
                    preview_path=image_path,
                    client=client,
                    model="qwen3-vl:8b-instruct",
                    proposal_id="lighting_review_unsafe",
                )
        self.assertEqual(len(client.requests), 2)

    def test_complete_categorical_contradiction_becomes_unresolved(self):
        inconsistent = json.dumps(
            {
                "schema_version": "0.1",
                "outcome": "proposed",
                "exposure": "balanced",
                "fill_balance": "balanced",
                "rim_separation": "balanced",
                "confidence": 0.72,
                "summary": "The frame needs a lighting correction.",
                "rationale": "The current result should be adjusted.",
                "missing_capabilities": [],
            }
        )
        client = FakeVisionClient([inconsistent, inconsistent])
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            image_path.write_bytes(HEALTHY_PNG)
            result = propose_lighting_rig_review(
                request="Review the applied rig.",
                context=review_context(),
                preview_path=image_path,
                client=client,
                model="qwen3-vl:8b-instruct",
                proposal_id="lighting_review_inconsistent",
            )

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.proposal.status, "unresolved")
        self.assertEqual(result.proposal.actions, [])
        self.assertIn("consistent categorical", result.proposal.summary)

    def test_loader_rejects_unknown_fields(self):
        value = review_context().model_dump(mode="json")
        value["invented"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(LightingRigReviewError, "invalid Unreal"):
                load_lighting_rig_review_context(path)


if __name__ == "__main__":
    unittest.main()

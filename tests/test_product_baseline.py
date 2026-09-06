import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from render_master_bot.cli import build_parser
from render_master_bot.product_baseline import (
    ProductBaselineError,
    _source_identity,
    load_capability_evidence_ledger,
    load_product_benchmark_suite,
)
from render_master_bot.serialization import canonical_sha256


def _benchmark_payload() -> dict:
    payload = {
        "schema_version": "0.1",
        "suite_id": "product_smoke_v1",
        "purpose": "Freeze representative local graphics assistant requests.",
        "status": "development_regression",
        "context_profiles": {
            "editor_empty": {
                "schema_version": "0.1",
                "project_name": "TestProject",
                "level_path": "/Game/Maps/TestLevel",
            }
        },
        "cases": [
            {
                "case_id": "capabilities_en",
                "category": "capability_qa",
                "language": "en",
                "evaluation_stage": "router_only",
                "request": "What can you do?",
                "context_profile_id": "editor_empty",
                "expectation": {
                    "outcome": "respond",
                    "intent": "answer_question",
                    "required_context": [],
                    "acceptance_checks": ["Explains at least one supported bounded workflow."],
                },
            }
        ],
    }
    payload["suite_sha256"] = canonical_sha256(payload)
    return payload


def _ledger_payload() -> dict:
    payload = {
        "schema_version": "0.1",
        "ledger_id": "capability_evidence_v1",
        "assessed_at_utc": "2026-09-05T00:00:00Z",
        "entries": [
            {
                "capability_id": "conversation",
                "implementation_status": "partial",
                "highest_evidence_level": "live_model_fixture",
                "assessment": "Routing works against bounded fixture contexts.",
                "evidence_paths": ["tests/test_assistant_conversation.py"],
                "acceptance_gaps": ["Tool results are not yet fed back into chat."],
            }
        ],
    }
    payload["ledger_sha256"] = canonical_sha256(payload)
    return payload


class ProductBaselineTests(unittest.TestCase):
    def test_frozen_benchmark_and_ledger_validate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            ledger_path = root / "ledger.json"
            suite_path.write_text(json.dumps(_benchmark_payload()), encoding="utf-8")
            ledger_path.write_text(json.dumps(_ledger_payload()), encoding="utf-8")

            suite = load_product_benchmark_suite(suite_path)
            ledger = load_capability_evidence_ledger(ledger_path)

        self.assertEqual(suite.cases[0].context_profile_id, "editor_empty")
        self.assertEqual(ledger.entries[0].highest_evidence_level, "live_model_fixture")

    def test_modified_benchmark_is_rejected_until_refrozen(self):
        payload = _benchmark_payload()
        payload["cases"][0]["request"] = "Describe all supported actions."
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ProductBaselineError, "suite_sha256"):
                load_product_benchmark_suite(path)

    def test_missing_and_unused_context_profiles_are_rejected(self):
        missing = _benchmark_payload()
        missing["cases"][0]["context_profile_id"] = "not_present"
        missing["suite_sha256"] = canonical_sha256(
            {key: value for key, value in missing.items() if key != "suite_sha256"}
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(missing), encoding="utf-8")
            with self.assertRaisesRegex(ProductBaselineError, "missing context"):
                load_product_benchmark_suite(path)

    def test_source_identity_includes_uncommitted_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            source = root / "source.txt"
            source.write_text("version one\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "source.txt"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=RenderMasterBot Tests",
                    "-c",
                    "user.email=tests@example.invalid",
                    "commit",
                    "-qm",
                    "baseline",
                ],
                check=True,
            )
            clean = _source_identity(root)
            source.write_text("version two\n", encoding="utf-8")
            dirty = _source_identity(root)

        self.assertFalse(clean.git_dirty)
        self.assertTrue(dirty.git_dirty)
        self.assertEqual(clean.git_head, dirty.git_head)
        self.assertNotEqual(clean.source_tree_sha256, dirty.source_tree_sha256)

    def test_cli_exposes_baseline_commands(self):
        args = build_parser().parse_args(
            [
                "product-baseline-capture",
                "Project.uproject",
                "--baseline-id",
                "baseline_v1",
                "--source-root",
                "repo",
                "--engine-root",
                "engine",
                "--assets",
                "assets.json",
                "--dataset",
                "dataset.json",
                "--benchmark-suite",
                "suite.json",
                "--evidence-ledger",
                "ledger.json",
                "--output",
                "baseline.json",
            ]
        )
        self.assertEqual(args.baseline_id, "baseline_v1")
        self.assertEqual(args.output, "baseline.json")


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from render_master_bot.cli import _configure_utf8_console, _write_json, build_parser


class CliOutputTests(unittest.TestCase):
    def test_json_output_creates_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "run" / "report.json"
            _write_json({"status": "ok"}, str(output))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"status": "ok"})

    def test_unreal_scan_parser_captures_deterministic_inputs(self):
        args = build_parser().parse_args(
            [
                "unreal-scan-assets",
                "E:/Project/Project.uproject",
                "--engine-root",
                "E:/Unreal Engine/UE_5.7",
                "--raw-output",
                "raw.json",
                "--output",
                "cards.json",
                "--limit",
                "12",
            ]
        )

        self.assertEqual(args.limit, 12)
        self.assertEqual(args.path_prefix, "/Game")
        self.assertEqual(args.raw_output, "raw.json")

    def test_unreal_build_parser_captures_validated_inputs(self):
        args = build_parser().parse_args(
            [
                "unreal-build-scene",
                "E:/Project/Project.uproject",
                "--engine-root",
                "E:/Unreal Engine/UE_5.7",
                "--spec",
                "scene.json",
                "--assets",
                "asset_cards.json",
                "--output",
                "scene_build.json",
                "--fail-on-warning",
            ]
        )

        self.assertEqual(args.spec, "scene.json")
        self.assertEqual(args.assets, "asset_cards.json")
        self.assertEqual(args.output, "scene_build.json")
        self.assertTrue(args.fail_on_warning)

    def test_plan_parser_enables_retrieval_constraints(self):
        args = build_parser().parse_args(
            ["plan", "--prompt", "add a door", "--retrieve-assets", "4"]
        )

        self.assertEqual(args.retrieve_assets, 4)

    def test_asset_search_parser_captures_query_and_limit(self):
        args = build_parser().parse_args(
            ["asset-search", "--query", "一扇门", "--limit", "3"]
        )

        self.assertEqual(args.query, "一扇门")
        self.assertEqual(args.limit, 3)

    def test_utf8_console_setup_tolerates_captured_streams(self):
        with patch("render_master_bot.cli.sys.stdout", new=StringIO()), patch(
            "render_master_bot.cli.sys.stderr",
            new=StringIO(),
        ):
            _configure_utf8_console()


if __name__ == "__main__":
    unittest.main()

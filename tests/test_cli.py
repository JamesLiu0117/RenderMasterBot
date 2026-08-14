import json
import tempfile
import unittest
from pathlib import Path

from render_master_bot.cli import _write_json, build_parser


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


if __name__ == "__main__":
    unittest.main()

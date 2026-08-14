import json
import tempfile
import unittest
from pathlib import Path

from render_master_bot.cli import _write_json


class CliOutputTests(unittest.TestCase):
    def test_json_output_creates_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "run" / "report.json"
            _write_json({"status": "ok"}, str(output))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()

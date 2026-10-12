import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from render_master_bot.assistant_level_audits import build_read_only_static_triage
from render_master_bot.assistant_level_profiling import build_level_profiling_run, analyze_level_profiling_run
from render_master_bot.profiling_export import export_report, public_summary, render_html
from test_assistant_level_profiling import _audit_report, _runtime_report, _gpu_report


class ProfilingExportTests(unittest.TestCase):
    def report(self):
        audit = _audit_report()
        runtime = _runtime_report()
        run = build_level_profiling_run(
            profiling_run_id="export_test", created_at_utc=datetime.now(timezone.utc),
            request="Private prompt must not be published", level_audit_report=audit,
            runtime_performance_report=runtime, gpu_scope_report=_gpu_report(runtime),
            actor_study_reports=[], skipped_actor_studies={
                item.assessment_id: "Fixture skip" for item in audit.assessments[:2]
            },
        )
        return analyze_level_profiling_run(run)

    def test_static_triage_is_read_only_and_recomputable(self):
        context = _audit_report().context
        result = build_read_only_static_triage(prompt="Disable all shadows", context=context)
        self.assertEqual(result.report.status, "review_only")
        self.assertEqual(result.report.actions, [])
        self.assertEqual(result.report.assessments, _audit_report().assessments)
        self.assertEqual(result.report.analyzed_by.provider, "local")
        self.assertFalse(result.report.modifies_editor_scene)

    def test_public_summary_omits_private_context(self):
        data = public_summary(self.report())
        serialized = json.dumps(data)
        for private in ("Private prompt", "actor_path", "world_path", "trace_file_name"):
            self.assertNotIn(private, serialized)
        self.assertEqual(data["status"], "partial")

    def test_html_escapes_external_strings(self):
        data = public_summary(self.report())
        data["gpu"] = '<script>alert("x")</script>'
        page = render_html(data)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("not evidence of an optimization speedup", page)

    def test_export_rejects_unverified_trace_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            report_path.write_text(self.report().model_dump_json(), encoding="utf-8")
            trace = root / "fake.utrace"
            trace.write_bytes(b"not a real trace")
            with self.assertRaisesRegex(ValueError, "Trace file does not match"):
                export_report(report_path, trace, root / "public")
            self.assertFalse((root / "public").exists())


if __name__ == "__main__":
    unittest.main()

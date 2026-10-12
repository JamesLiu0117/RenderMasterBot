"""Portable, escaped public summaries of validated, trace-backed profiling runs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

from render_master_bot.contracts import AssistantLevelProfilingReport


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def public_summary(report: AssistantLevelProfilingReport) -> dict:
    """Whitelist public fields; never publish local paths, prompts or Actor paths."""
    runtime = report.run.runtime_performance_report.capture
    gpu = report.run.gpu_scope_report.capture
    queues = {queue.queue_id: queue.display_name for queue in gpu.queues}
    return {
        "schema_version": "1.0",
        "report_id": report.report_id,
        "status": report.status,
        "runtime_review_status": report.run.runtime_performance_report.status,
        "gpu_review_status": report.run.gpu_scope_report.status,
        "captured_at_utc": runtime.captured_at_utc.isoformat(),
        "gpu": runtime.gpu_name,
        "mode": runtime.capture_mode,
        "viewport": [runtime.viewport_width_px, runtime.viewport_height_px],
        "warmup_frames": runtime.warmup_frames,
        "sample_count": runtime.sample_count,
        "frame_p95_ms": report.frame_p95_ms,
        "gpu_p95_ms": report.gpu_p95_ms,
        "frame_budget_ms": runtime.target_frame_ms,
        "frame_budget_miss_fraction": report.frame_budget_miss_fraction,
        "captured_static_mesh_actors": report.run.level_audit_report.statistics.captured_actor_count,
        "measured_actor_count": report.measured_actor_count,
        "skipped_actor_count": report.skipped_actor_count,
        "stable_actor_candidate_count": report.stable_actor_candidate_count,
        "trace_duration_seconds": gpu.captured_duration_seconds,
        "trace_sha256": gpu.trace_sha256,
        "trace_size_bytes": gpu.trace_file_size_bytes,
        "run_sha256": report.run_sha256,
        "gpu_scopes": [
            {"name": scope.name, "queue": queues[scope.queue_id],
             "inclusive_ms_per_second": scope.total_inclusive_ms / gpu.captured_duration_seconds,
             "instances": scope.instance_count}
            for scope in gpu.scopes[:10]
        ],
        "measurement_notes": [
            "One Editor PIE/SIE workload; not a packaged-build benchmark.",
            "Inclusive nested GPU scopes overlap and must not be summed.",
            "A captured baseline is not evidence of an optimization speedup.",
            "Static Actor coverage excludes unsupported component and Actor types.",
            "Zero measured Actors does not prove that the scene has no bottlenecks.",
            "Trace hashes prove file identity, not measurement quality or causal attribution.",
        ],
        "production_scene_modified": False,
    }


def render_html(data: dict) -> str:
    esc = lambda value: html.escape(str(value), quote=True)
    gpu_p95 = "Unavailable" if data["gpu_p95_ms"] is None else f'{data["gpu_p95_ms"]:.2f} ms'
    cards = [
        ("FRAME P95", f'{data["frame_p95_ms"]:.2f} ms'),
        ("GPU P95", gpu_p95),
        ("FRAMES SAMPLED", data["sample_count"]),
        ("BUDGET MISSES", f'{data["frame_budget_miss_fraction"] * 100:.1f}%'),
    ]
    card_html = "".join(f'<div class="card"><small>{esc(label)}</small><strong>{esc(value)}</strong></div>' for label, value in cards)
    rows = "".join(
        f'<tr><td>{esc(row["name"])}</td><td>{esc(row["queue"])}</td><td>{row["inclusive_ms_per_second"]:.2f}</td><td>{row["instances"]}</td></tr>'
        for row in data["gpu_scopes"]
    )
    notes = "".join(f'<li>{esc(note)}</li>' for note in data["measurement_notes"])
    return f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RenderMasterBot — measured profiling report</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#0c1320;color:#e6edf7;font:16px/1.65 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:64px 28px}}.eyebrow,small{{color:#7de1d4;letter-spacing:.14em;font-size:12px}}h1{{font-size:clamp(32px,5vw,56px);line-height:1.1;letter-spacing:-.04em;margin:16px 0}}h2{{font-size:24px;margin-top:46px}}p,li{{color:#b9c6d9}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:36px 0}}.card{{background:#142238;border:1px solid #29405d;border-radius:14px;padding:22px}}strong{{display:block;font-size:28px;margin-top:12px}}table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{text-align:left;padding:13px;border-bottom:1px solid #29405d}}th{{color:#7de1d4}}.table{{overflow:auto}}code{{overflow-wrap:anywhere;color:#a4c5fc}}a{{color:#7de1d4}}.badge{{display:inline-block;border:1px solid #3c756f;border-radius:20px;padding:4px 14px;color:#7de1d4}}footer{{margin-top:48px;border-top:1px solid #29405d;padding-top:24px;font-size:13px}}@media(max-width:720px){{.grid{{grid-template-columns:repeat(2,1fr)}}main{{padding:32px 18px}}}}
</style><main>
<div class="eyebrow">LOCAL GRAPHICS ASSISTANT / EVIDENCE REPORT</div>
<h1>From conversation<br>to measured rendering evidence.</h1>
<p>RenderMasterBot coordinates Unreal sampling and Unreal Insights capture, validates the evidence, and returns a traceable report. This page shows one recorded workload, not a promised performance gain.</p>
<span class="badge">{esc(data['status'].upper())} · TRACE FILE VERIFIED</span>
<div class="grid">{card_html}</div>
<h2>Capture conditions</h2>
<p>{esc(data['gpu'])} · {esc(data['mode'].upper())} · {data['viewport'][0]} × {data['viewport'][1]} viewport<br>
{data['warmup_frames']} warmup frames · {data['sample_count']} measured frames · {data['trace_duration_seconds']:.3f} s trace · {data['frame_budget_ms']:.2f} ms frame budget<br>{esc(data['captured_at_utc'])}</p>
<h2>Leading GPU scopes</h2><p>Inclusive queue-local time, normalized to milliseconds per second of capture. Nested scopes overlap.</p>
<div class="table"><table><thead><tr><th>Scope</th><th>Queue</th><th>Inclusive ms/s</th><th>Instances</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Coverage and boundaries</h2>
<p>{data['captured_static_mesh_actors']} eligible static-mesh Actors captured · {data['measured_actor_count']} Actor studies · {data['skipped_actor_count']} skipped candidates · {data['stable_actor_candidate_count']} stable candidates. No production scene modification.</p>
<p>Runtime review: {esc(data['runtime_review_status'])}. GPU review: {esc(data['gpu_review_status'])}. A partial report is not a completed investigation; consult the private evidence for unresolved or skipped details before making performance claims.</p>
<ul>{notes}</ul>
<h2>Evidence identity</h2><p>Trace SHA-256<br><code>{esc(data['trace_sha256'])}</code></p>
<p>Validated run SHA-256<br><code>{esc(data['run_sha256'])}</code></p>
<footer>Local paths, prompts and Actor paths are omitted from this public summary. Original evidence stays local. <a href="summary.json">Download summary JSON</a>.</footer>
</main></html>'''


def export_report(report_path: Path, trace_path: Path, output_dir: Path) -> dict:
    report = AssistantLevelProfilingReport.model_validate_json(report_path.read_text(encoding="utf-8-sig"))
    capture = report.run.gpu_scope_report.capture
    if trace_path.stat().st_size != capture.trace_file_size_bytes or file_sha256(trace_path) != capture.trace_sha256:
        raise ValueError("Trace file does not match the validated report; export refused")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Export directory must be empty; existing artifacts are preserved")
    data = public_summary(report)
    data["source_report_sha256"] = file_sha256(report_path)
    data["trace_file_verified"] = True
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(render_html(data), encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    export_report(args.report, args.trace, args.output_dir)
    print(f"Exported validated report to {args.output_dir}")


if __name__ == "__main__":
    main()

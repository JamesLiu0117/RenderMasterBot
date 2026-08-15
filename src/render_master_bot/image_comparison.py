"""Deterministic safeguards for iterative preview corrections."""

from __future__ import annotations

from render_master_bot.contracts import ImageComparison, ImageStatistics


def compare_image_statistics(
    baseline: ImageStatistics,
    candidate: ImageStatistics,
) -> ImageComparison:
    """Classify only large, defensible changes; leave subtle quality to vision."""

    regressions: list[str] = []
    improvements: list[str] = []
    for field, label in (
        ("blank_like", "blank-like frame"),
        ("underexposed_like", "underexposed-like frame"),
        ("overexposed_like", "overexposed-like frame"),
    ):
        before = getattr(baseline, field)
        after = getattr(candidate, field)
        if after and not before:
            regressions.append(f"Candidate introduced a {label} classification.")
        elif before and not after:
            improvements.append(f"Candidate removed the {label} classification.")

    if (
        baseline.center_luminance >= 0.05
        and candidate.center_luminance < baseline.center_luminance * 0.4
    ):
        regressions.append(
            "Candidate center luminance fell below 40% of the baseline."
        )
    if (
        baseline.foreground_fraction >= 0.02
        and candidate.foreground_fraction < baseline.foreground_fraction * 0.5
    ):
        regressions.append(
            "Candidate foreground fraction fell below 50% of the baseline."
        )

    center_is_healthy = 0.08 <= candidate.center_luminance <= 0.85
    center_was_healthy = 0.08 <= baseline.center_luminance <= 0.85
    if center_is_healthy and not center_was_healthy:
        improvements.append("Candidate moved center luminance into the healthy range.")
    if (
        baseline.foreground_fraction >= 0.02
        and candidate.foreground_fraction > baseline.foreground_fraction * 1.25
    ):
        improvements.append("Candidate increased foreground fraction by more than 25%.")

    if regressions:
        outcome = "regressed"
        reasons = regressions
    elif improvements:
        outcome = "improved"
        reasons = improvements
    else:
        outcome = "inconclusive"
        reasons = ["No deterministic large-change threshold was crossed."]

    return ImageComparison(
        baseline_sha256=baseline.sha256,
        candidate_sha256=candidate.sha256,
        outcome=outcome,
        reasons=reasons,
        mean_luminance_delta=(
            candidate.mean_luminance - baseline.mean_luminance
        ),
        center_luminance_delta=(
            candidate.center_luminance - baseline.center_luminance
        ),
        foreground_fraction_delta=(
            candidate.foreground_fraction - baseline.foreground_fraction
        ),
    )

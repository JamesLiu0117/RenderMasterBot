import unittest

from render_master_bot.contracts import ImageStatistics
from render_master_bot.image_comparison import compare_image_statistics


def stats(sha_char: str, **overrides) -> ImageStatistics:
    value = {
        "sha256": sha_char * 64,
        "width_px": 640,
        "height_px": 360,
        "sampled_pixels": 10_000,
        "mean_luminance": 0.2,
        "luminance_stddev": 0.1,
        "p05_luminance": 0.01,
        "p95_luminance": 0.7,
        "dark_pixel_fraction": 0.4,
        "clipped_pixel_fraction": 0.0,
        "foreground_fraction": 0.3,
        "center_luminance": 0.4,
        "border_luminance": 0.01,
        "blank_like": False,
        "underexposed_like": False,
        "overexposed_like": False,
    }
    value.update(overrides)
    return ImageStatistics.model_validate(value)


class ImageComparisonTests(unittest.TestCase):
    def test_large_center_and_foreground_loss_is_regression(self):
        result = compare_image_statistics(
            stats("a"),
            stats("b", center_luminance=0.1, foreground_fraction=0.1),
        )

        self.assertEqual(result.outcome, "regressed")
        self.assertEqual(len(result.reasons), 2)

    def test_removed_underexposure_is_improvement(self):
        result = compare_image_statistics(
            stats("a", underexposed_like=True, center_luminance=0.04),
            stats("b", underexposed_like=False, center_luminance=0.2),
        )

        self.assertEqual(result.outcome, "improved")

    def test_small_change_is_inconclusive(self):
        result = compare_image_statistics(
            stats("a"),
            stats("b", mean_luminance=0.21, center_luminance=0.39),
        )

        self.assertEqual(result.outcome, "inconclusive")

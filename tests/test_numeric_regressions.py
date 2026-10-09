"""Small, independent exact-reference checks for difficult numeric inputs.

The oracle fits the floats actually represented by CSV parsing, not an idealized
decimal experiment. It uses pairwise differences for OLS, exact Fraction sums
for residuals and repeatability, and Decimal only to round square roots. Fixtures
are synthetic. No production numeric helper is imported or reused.
"""

from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import combinations, permutations
import json
import math
import unittest

from calibration.core import CalibrationError, analyze_csv


def as_fraction(value):
    return Fraction.from_float(float(value))


def rounded_sqrt(value):
    """Round an exact nonnegative rational square root to a Python float."""
    with localcontext() as context:
        context.prec = 120
        quotient = Decimal(value.numerator) / Decimal(value.denominator)
        return float(quotient.sqrt())


def exact_reference(pairs):
    """Use pairwise identities, independent of an OLS centered-moment loop."""
    points = [(as_fraction(x), as_fraction(y)) for x, y in pairs]
    cross = Fraction(0)
    spread = Fraction(0)
    for (x_left, y_left), (x_right, y_right) in combinations(points, 2):
        cross += (x_left - x_right) * (y_left - y_right)
        spread += (x_left - x_right) ** 2
    slope = cross / spread
    intercept = sum((y - slope * x for x, y in points), Fraction(0)) / len(points)
    predictions = [slope * x + intercept for x, _ in points]
    residuals = [y - fitted for (_, y), fitted in zip(points, predictions)]
    sse = sum((residual ** 2 for residual in residuals), Fraction(0))
    mean_y = sum((y for _, y in points), Fraction(0)) / len(points)
    sst = sum(((y - mean_y) ** 2 for _, y in points), Fraction(0))

    grouped = {}
    for x, y in points:
        grouped.setdefault(x, []).append(y)
    groups = {}
    within_sum = Fraction(0)
    degrees_of_freedom = 0
    for x, values in grouped.items():
        mean = sum(values, Fraction(0)) / len(values)
        group_sum = sum(((y - mean) ** 2 for y in values), Fraction(0))
        degrees_of_freedom += len(values) - 1
        within_sum += group_sum
        groups[float(x)] = {
            "count": len(values), "mean_y": float(mean),
            "std_y": rounded_sqrt(group_sum / (len(values) - 1)) if len(values) > 1 else None,
        }
    return {
        "fit": {"slope": float(slope), "intercept": float(intercept),
                "r_squared": float(1 - sse / sst) if sst else None},
        "metrics": {"rmse_y": rounded_sqrt(sse / len(points)),
                    "residual_std_y": rounded_sqrt(sse / (len(points) - 2)),
                    "max_abs_residual_y": float(max(map(abs, residuals)))},
        "residuals": [float(value) for value in residuals],
        "predictions": [float(value) for value in predictions],
        "groups": groups,
        "pooled_std_y": rounded_sqrt(within_sum / degrees_of_freedom) if degrees_of_freedom else None,
        "degrees_of_freedom": degrees_of_freedom,
    }


def csv_text(pairs):
    return "x,y\n" + "".join("{!r},{!r}\n".format(float(x), float(y)) for x, y in pairs)


class NumericRegressionTests(unittest.TestCase):
    def assert_close(self, actual, expected, label):
        if expected is None:
            self.assertIsNone(actual, label)
            return
        self.assertIsNotNone(actual, label)
        self.assertTrue(math.isfinite(actual), label)
        # Relative checks keep genuinely tiny errors visible, unlike a fixed
        # seven-decimal-place assertion. A few ulps handle final float rounding.
        tolerance = max(abs(expected) * 1e-12, math.ulp(expected) * 8)
        self.assertLessEqual(abs(actual - expected), tolerance,
                             "{}: actual={!r}, exact-rounded={!r}".format(label, actual, expected))

    def assert_matches_reference(self, pairs):
        expected = exact_reference(pairs)
        result = analyze_csv(csv_text(pairs), "x", "y")
        for section in ("fit", "metrics"):
            for key, value in expected[section].items():
                self.assert_close(result[section][key], value, section + "." + key)
        for index, point in enumerate(result["points"]):
            self.assert_close(point["predicted_y"], expected["predictions"][index], "prediction[{}]".format(index))
            self.assert_close(point["residual_y"], expected["residuals"][index], "residual[{}]".format(index))
        repeatability = result["repeatability"]
        self.assertEqual(repeatability["degrees_of_freedom"], expected["degrees_of_freedom"])
        self.assert_close(repeatability["pooled_std_y"], expected["pooled_std_y"], "pooled repeatability")
        self.assertEqual(len(repeatability["groups"]), len(expected["groups"]))
        for group in repeatability["groups"]:
            wanted = expected["groups"][group["x"]]
            self.assertEqual(group["count"], wanted["count"])
            self.assert_close(group["mean_y"], wanted["mean_y"], "group mean")
            self.assert_close(group["std_y"], wanted["std_y"], "group standard deviation")
        json.dumps(result, allow_nan=False)
        return result

    def test_wide_span_preserves_small_nonzero_residuals(self):
        pairs = [(1e16, 1e16), (0, 0), (1, 2)]
        result = self.assert_matches_reference(pairs)
        # The exact reference remains independently accountable to familiar
        # limiting values; this is not a perfect calibration despite R² -> 1.
        self.assertAlmostEqual(result["metrics"]["rmse_y"], math.sqrt(1 / 6), places=14)
        self.assertAlmostEqual(result["metrics"]["residual_std_y"], math.sqrt(1 / 2), places=14)
        self.assertGreater(result["metrics"]["rmse_y"], 0)

    def test_all_orders_of_wide_span_points_have_identical_metrics(self):
        pairs = [(1e16, 1e16), (0, 0), (1, 2)]
        baseline = None
        for order in permutations(pairs):
            with self.subTest(order=order):
                result = self.assert_matches_reference(order)
                summary = {key: result[key] for key in ("fit", "metrics", "repeatability")}
                if baseline is None:
                    baseline = summary
                else:
                    self.assertEqual(summary, baseline)

    def test_large_offsets_on_both_axes_match_exact_parsed_values(self):
        self.assert_matches_reference([
            (1e16, -1e16), (1e16 + 2, -1e16 + 2),
            (1e16 + 4, -1e16 + 6), (1e16 + 6, -1e16 + 6),
        ])

    def test_decimal_repeatability_uses_exact_float_inputs(self):
        pairs = [
            (0, 0.1), (0, 0.10000000000000002), (0, 0.10000000000000003),
            (1, 0.2), (1, 0.20000000000000004), (2, 0.3),
        ]
        result = self.assert_matches_reference(pairs)
        reversed_result = self.assert_matches_reference(list(reversed(pairs)))
        self.assertGreater(result["repeatability"]["pooled_std_y"], 0)
        self.assertEqual(result["repeatability"], reversed_result["repeatability"])
        self.assertEqual(result["metrics"], reversed_result["metrics"])

    def test_tiny_but_representable_output_errors_match_exact_reference(self):
        self.assert_matches_reference([
            (0, 1e-200), (0, 3e-200), (1, 2e-200),
            (1, 4e-200), (2, 5e-200), (2, 5e-200),
        ])

    def test_zero_correlation_is_distinguished_from_constant_output(self):
        result = self.assert_matches_reference([(-1, 1), (0, 0), (1, 1)])
        self.assertEqual(result["fit"]["slope"], 0)
        self.assertEqual(result["fit"]["r_squared"], 0)
        self.assertGreater(result["metrics"]["rmse_y"], 0)

    def test_nonzero_text_underflow_is_excluded_not_silently_zero(self):
        for invalid_row in ("1e-9999,7", "3,-1e-9999"):
            with self.subTest(invalid_row=invalid_row):
                result = analyze_csv("x,y\n0,1\n1,3\n2,5\n" + invalid_row + "\n", "x", "y")
                self.assertEqual(result["counts"], {"total": 4, "valid": 3, "excluded": 1})
                self.assertEqual(result["excluded_rows"][0]["source_line"], 5)
                self.assertIn("下溢", result["excluded_rows"][0]["reason"])
                self.assertEqual(result["fit"]["slope"], 2)

    def test_actual_zero_with_extreme_exponent_remains_valid(self):
        result = analyze_csv("x,y\n0e-9999,1\n1,3\n2,5\n", "x", "y")
        self.assertEqual(result["counts"], {"total": 3, "valid": 3, "excluded": 0})
        self.assertEqual(result["fit"]["slope"], 2)

    def test_nonzero_computed_values_that_underflow_are_explicitly_rejected(self):
        for text in (
            "x,y\n0,0\n5e99,5e-324\n1e100,1e-323\n",  # Nonzero slope cannot be represented.
            "x,y\n0,0\n0,5e-324\n1,5e-324\n1,1e-323\n",  # Group mean / residual rounds to zero.
        ):
            with self.subTest(text=text):
                with self.assertRaisesRegex(CalibrationError, "下溢|表示|范围"):
                    analyze_csv(text, "x", "y")

    def test_finite_input_with_unrepresentable_slope_is_explicitly_rejected(self):
        with self.assertRaisesRegex(CalibrationError, "溢出|表示|范围"):
            analyze_csv("x,y\n0,0\n1e-320,1\n2e-320,2\n", "x", "y")


if __name__ == "__main__":
    unittest.main()

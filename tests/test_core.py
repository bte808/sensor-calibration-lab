"""Public-API tests using independently hand-calculable calibration examples.

All measurements below are synthetic mathematical fixtures, not hardware data.
Run with: python3 -m unittest discover -s tests -v
"""

import datetime
import hashlib
import json
import math
import unittest

from calibration.core import (
    APP_VERSION,
    CalibrationError,
    analyze_csv,
    create_bundle,
    inspect_csv,
)


PERFECT_CSV = "reference,output\n0,1\n1,3\n2,5\n"


class RegressionTests(unittest.TestCase):
    def test_hand_calculated_ols_residuals_and_error_denominators(self):
        # x mean = 1, y mean = 10/3, Sxx = 4, Sxy = 6.
        # SSE = 13/3 and SST = 40/3, independently evaluated as fractions.
        result = analyze_csv(
            "reference,output\n0,1\n0,3\n1,2\n1,4\n2,5\n2,5\n",
            "reference", "output", " °C ", " V ",
        )
        self.assertEqual(result["columns"], {"x": "reference", "y": "output"})
        self.assertEqual(result["units"], {"x": "°C", "y": "V"})
        self.assertEqual(result["counts"], {"total": 6, "valid": 6, "excluded": 0})
        self.assertAlmostEqual(result["fit"]["slope"], 3 / 2)
        self.assertAlmostEqual(result["fit"]["intercept"], 11 / 6)
        self.assertAlmostEqual(result["fit"]["r_squared"], 27 / 40)
        self.assertAlmostEqual(result["metrics"]["rmse_y"], math.sqrt(13 / 18))
        self.assertAlmostEqual(result["metrics"]["residual_std_y"], math.sqrt(13 / 12))
        self.assertAlmostEqual(result["metrics"]["max_abs_residual_y"], 4 / 3)
        expected_residuals = [-5 / 6, 7 / 6, -4 / 3, 2 / 3, 1 / 6, 1 / 6]
        for point, residual in zip(result["points"], expected_residuals):
            self.assertAlmostEqual(point["residual_y"], residual)
            self.assertAlmostEqual(point["predicted_y"], point["y"] - residual)
        self.assertEqual([p["source_line"] for p in result["points"]], list(range(2, 8)))

    def test_perfect_calibration(self):
        result = analyze_csv(PERFECT_CSV, "reference", "output")
        self.assertAlmostEqual(result["fit"]["slope"], 2)
        self.assertAlmostEqual(result["fit"]["intercept"], 1)
        self.assertAlmostEqual(result["fit"]["r_squared"], 1)
        self.assertAlmostEqual(result["metrics"]["rmse_y"], 0)
        self.assertAlmostEqual(result["metrics"]["residual_std_y"], 0)

    def test_negative_sensitivity_is_allowed(self):
        result = analyze_csv("x,y\n0,4\n1,2\n2,0\n", "x", "y")
        self.assertAlmostEqual(result["fit"]["slope"], -2)
        self.assertAlmostEqual(result["fit"]["intercept"], 4)
        self.assertAlmostEqual(result["fit"]["r_squared"], 1)

    def test_constant_output_has_undefined_r_squared_but_zero_error(self):
        result = analyze_csv("x,y\n0,7\n0,7\n1,7\n1,7\n", "x", "y")
        self.assertAlmostEqual(result["fit"]["slope"], 0)
        self.assertAlmostEqual(result["fit"]["intercept"], 7)
        self.assertIsNone(result["fit"]["r_squared"])
        for metric in result["metrics"].values():
            self.assertAlmostEqual(metric, 0)
        self.assertEqual(result["repeatability"]["degrees_of_freedom"], 2)
        self.assertAlmostEqual(result["repeatability"]["pooled_std_y"], 0)
        self.assertTrue(result["warnings"])

    def test_constant_decimal_output_is_not_mistaken_for_variation(self):
        result = analyze_csv("x,y\n0,0.1\n0,0.1\n0,0.1\n1,0.1\n1,0.1\n1,0.1\n", "x", "y")
        self.assertIsNone(result["fit"]["r_squared"])
        self.assertEqual(result["fit"]["slope"], 0)
        self.assertEqual(result["fit"]["intercept"], 0.1)
        self.assertEqual(result["metrics"]["rmse_y"], 0)
        self.assertEqual(result["repeatability"]["pooled_std_y"], 0)
        for group in result["repeatability"]["groups"]:
            self.assertEqual(group["mean_y"], 0.1)
            self.assertEqual(group["std_y"], 0)

    def test_large_reference_offset_preserves_small_span(self):
        # A direct sum-of-squares implementation loses the variance here.
        text = "x,y\n1000000000000,2\n1000000000001,5\n1000000000002,8\n1000000000003,11\n"
        result = analyze_csv(text, "x", "y")
        self.assertAlmostEqual(result["fit"]["slope"], 3, places=12)
        self.assertAlmostEqual(result["fit"]["intercept"], 2 - 3e12, places=3)
        self.assertLess(result["metrics"]["rmse_y"], 1e-10)
        self.assertAlmostEqual(result["fit"]["r_squared"], 1)

    def test_reference_mean_need_not_be_representable_at_large_offset(self):
        # Relative x values [0, 2, 2] have mean 4/3, but 1e16 + 4/3
        # cannot be represented. In relative coordinates Sxy=2/3, Sxx=8/3.
        result = analyze_csv("x,y\n10000000000000000,0\n10000000000000002,0\n10000000000000002,1\n", "x", "y")
        self.assertAlmostEqual(result["fit"]["slope"], 0.25)
        self.assertAlmostEqual(result["fit"]["r_squared"], 0.25)
        self.assertAlmostEqual(result["metrics"]["rmse_y"], math.sqrt(0.5 / 3))

    def test_tiny_reference_span_does_not_underflow_to_zero_variance(self):
        # Squaring these reference values underflows, yet the slope is finite.
        result = analyze_csv("x,y\n0,1\n1e-200,3\n2e-200,5\n", "x", "y")
        self.assertTrue(math.isclose(result["fit"]["slope"], 2e200, rel_tol=1e-12))
        self.assertAlmostEqual(result["fit"]["intercept"], 1)
        self.assertLess(result["metrics"]["rmse_y"], 1e-12)

    def test_tiny_output_residuals_and_repeatability_do_not_underflow(self):
        # Same hand-calculated fixture as above, with outputs scaled by 1e-200.
        result = analyze_csv("x,y\n0,1e-200\n0,3e-200\n1,2e-200\n1,4e-200\n2,5e-200\n2,5e-200\n", "x", "y")
        self.assertAlmostEqual(result["fit"]["r_squared"], 27 / 40)
        self.assertAlmostEqual(result["metrics"]["rmse_y"] / 1e-200, math.sqrt(13 / 18))
        self.assertAlmostEqual(result["metrics"]["residual_std_y"] / 1e-200, math.sqrt(13 / 12))
        self.assertAlmostEqual(result["repeatability"]["pooled_std_y"] / 1e-200, math.sqrt(4 / 3))

    def test_large_finite_values_remain_json_serializable(self):
        result = analyze_csv("x,y\n-1e100,-5e99\n0,0\n1e100,5e99\n", "x", "y")
        self.assertAlmostEqual(result["fit"]["slope"], 0.5)
        self.assertAlmostEqual(result["fit"]["r_squared"], 1)
        json.dumps(result, allow_nan=False)

    def test_outlier_is_retained_without_automatic_deletion(self):
        result = analyze_csv("x,y\n0,0\n1,1\n2,2\n3,1000\n", "x", "y")
        self.assertEqual(result["counts"]["valid"], 4)
        self.assertEqual(result["counts"]["excluded"], 0)
        self.assertEqual(result["points"][-1]["y"], 1000)


class RepeatabilityTests(unittest.TestCase):
    def test_unequal_groups_use_pooled_within_group_degrees_of_freedom(self):
        # First group: mean 2, SSE 8, df 2, sample SD 2.
        # Second group: mean 7, SSE 8, df 1, sample SD sqrt(8).
        # Singleton: mean 10, df 0. Pooled SD = sqrt(16/3).
        result = analyze_csv("x,y\n0,0\n0,2\n0,4\n1,5\n1,9\n2,10\n", "x", "y")
        repeat = result["repeatability"]
        self.assertEqual(repeat["degrees_of_freedom"], 3)
        self.assertEqual(repeat["repeated_groups"], 2)
        self.assertAlmostEqual(repeat["pooled_std_y"], math.sqrt(16 / 3))
        groups = {group["x"]: group for group in repeat["groups"]}
        self.assertEqual(set(groups), {0, 1, 2})
        self.assertEqual(groups[0]["count"], 3)
        self.assertAlmostEqual(groups[0]["mean_y"], 2)
        self.assertAlmostEqual(groups[0]["std_y"], 2)
        self.assertEqual(groups[1]["count"], 2)
        self.assertAlmostEqual(groups[1]["mean_y"], 7)
        self.assertAlmostEqual(groups[1]["std_y"], math.sqrt(8))
        self.assertEqual(groups[2]["count"], 1)
        self.assertAlmostEqual(groups[2]["mean_y"], 10)
        self.assertIsNone(groups[2]["std_y"])

    def test_without_repeats_std_is_null_not_zero(self):
        result = analyze_csv(PERFECT_CSV, "reference", "output")
        repeat = result["repeatability"]
        self.assertIsNone(repeat["pooled_std_y"])
        self.assertEqual(repeat["degrees_of_freedom"], 0)
        self.assertEqual(repeat["repeated_groups"], 0)
        self.assertTrue(all(group["std_y"] is None for group in repeat["groups"]))
        self.assertTrue(result["warnings"])

    def test_grouping_uses_exact_parsed_numeric_value(self):
        result = analyze_csv("x,y\n1,1\n1.0,3\n1.0000000001,2\n2,5\n", "x", "y")
        repeat = result["repeatability"]
        self.assertEqual(len(repeat["groups"]), 3)
        self.assertEqual(repeat["repeated_groups"], 1)
        self.assertEqual(repeat["degrees_of_freedom"], 1)
        self.assertAlmostEqual(repeat["pooled_std_y"], math.sqrt(2))

    def test_two_reference_levels_warn_even_when_n_is_large(self):
        result = analyze_csv("x,y\n0,0\n0,1\n0,2\n1,3\n1,4\n1,5\n", "x", "y")
        self.assertTrue(result["warnings"])


class CsvTests(unittest.TestCase):
    def test_supported_delimiters_and_utf8_bom(self):
        for delimiter in (",", "\t", ";"):
            with self.subTest(delimiter=repr(delimiter)):
                text = "\ufeff" + "\r\n".join(
                    delimiter.join(row)
                    for row in (("reference", "output"), ("0", "1"), ("1", "3"), ("2", "5"))
                )
                inspected = inspect_csv(text)
                self.assertEqual(inspected["headers"], ["reference", "output"])
                self.assertEqual(inspected["delimiter"], delimiter)
                self.assertEqual(inspected["row_count"], 3)
                self.assertEqual(inspected["preview"][0], ["0", "1"])
                self.assertAlmostEqual(analyze_csv(text, "reference", "output")["fit"]["slope"], 2)

    def test_preview_is_limited_to_eight_data_records(self):
        text = "x,y\n" + "".join("{},{}\n".format(i, 2 * i) for i in range(12))
        inspected = inspect_csv(text)
        self.assertEqual(inspected["row_count"], 12)
        self.assertEqual(len(inspected["preview"]), 8)

    def test_blank_records_do_not_change_data_count_or_source_line(self):
        text = "x,y\n\n0,1\n\n1,3\n2,5\n\n"
        inspected = inspect_csv(text)
        self.assertEqual(inspected["row_count"], 3)
        result = analyze_csv(text, "x", "y")
        self.assertEqual(result["counts"]["total"], 3)
        self.assertEqual([point["source_line"] for point in result["points"]], [3, 5, 6])

    def test_multiline_quoted_cells_use_physical_end_line(self):
        text = 'x,y,note\n0,1,"first\nsecond"\n1,3,"a,b"\n2,5,plain\n'
        inspected = inspect_csv(text)
        self.assertEqual(inspected["row_count"], 3)
        self.assertEqual(inspected["preview"][0][2], "first\nsecond")
        result = analyze_csv(text, "x", "y")
        self.assertEqual([point["source_line"] for point in result["points"]], [3, 4, 5])

    def test_empty_or_duplicate_headers_are_rejected(self):
        for text in ("x,x\n0,1\n", "x,\n0,1\n", ",y\n0,1\n", "x, x \n0,1\n"):
            with self.subTest(text=text):
                with self.assertRaises(CalibrationError):
                    inspect_csv(text)

    def test_empty_and_header_only_inputs_are_rejected(self):
        for text in ("", "\ufeff", "\n\n", "x,y", "x,y\n\n"):
            with self.subTest(text=text):
                with self.assertRaises(CalibrationError):
                    inspect_csv(text)

    def test_inconsistent_record_width_is_rejected(self):
        for text in ("x,y\n0,1\n1\n2,5\n", "x,y\n0,1\n1,3,extra\n2,5\n"):
            with self.subTest(text=text):
                with self.assertRaises(CalibrationError):
                    inspect_csv(text)

    def test_record_limit_is_enforced(self):
        text = "x,y\n" + "0,1\n" * 20000
        self.assertEqual(inspect_csv(text)["row_count"], 20000)
        with self.assertRaises(CalibrationError):
            inspect_csv(text + "0,1\n")

    def test_column_limit_is_enforced(self):
        text = ",".join("c{}".format(i) for i in range(50)) + "\n" + ",".join(["0"] * 50)
        self.assertEqual(len(inspect_csv(text)["headers"]), 50)
        oversized = ",".join("c{}".format(i) for i in range(51)) + "\n" + ",".join(["0"] * 51)
        with self.assertRaises(CalibrationError):
            inspect_csv(oversized)

    def test_selected_invalid_values_are_excluded_with_source_lines(self):
        text = "x,y,unused\n 0 , 1 ,nan\n1,3,\n2,5,anything\n,7,ok\n3,,ok\noops,9,ok\n4,text,ok\n"
        result = analyze_csv(text, "x", "y")
        self.assertEqual(result["counts"], {"total": 7, "valid": 3, "excluded": 4})
        self.assertEqual([row["source_line"] for row in result["excluded_rows"]], [5, 6, 7, 8])
        self.assertTrue(all(row["reason"] for row in result["excluded_rows"]))
        self.assertAlmostEqual(result["fit"]["slope"], 2)

    def test_nonfinite_and_out_of_range_values_are_excluded(self):
        invalid = ("nan", "NaN", "inf", "-Infinity", "1e101", "-1e101", "1e9999")
        for value in invalid:
            for selected in ("x", "y"):
                with self.subTest(value=value, selected=selected):
                    row = "{},7".format(value) if selected == "x" else "3,{}".format(value)
                    text = "x,y\n0,1\n1,3\n2,5\n" + row + "\n"
                    result = analyze_csv(text, "x", "y")
                    self.assertEqual(result["counts"], {"total": 4, "valid": 3, "excluded": 1})
                    self.assertEqual(result["excluded_rows"][0]["source_line"], 5)
                    json.dumps(result, allow_nan=False)


class ValidationTests(unittest.TestCase):
    def test_at_least_three_valid_pairs_are_required(self):
        for text in ("x,y\n0,1\n1,3\n", "x,y\n0,1\n1,3\n2,invalid\n"):
            with self.subTest(text=text):
                with self.assertRaises(CalibrationError):
                    analyze_csv(text, "x", "y")

    def test_constant_reference_is_rejected(self):
        with self.assertRaises(CalibrationError):
            analyze_csv("x,y\n1,1\n1,2\n1,3\n", "x", "y")

    def test_unknown_and_identical_selected_columns_are_rejected(self):
        for x_column, y_column in (("reference", "reference"), ("unknown", "output"), ("reference", "unknown")):
            with self.subTest(x_column=x_column, y_column=y_column):
                with self.assertRaises(CalibrationError):
                    analyze_csv(PERFECT_CSV, x_column, y_column)

    def test_unit_length_limit_and_trimming(self):
        result = analyze_csv(PERFECT_CSV, "reference", "output", "u" * 32, "  V  ")
        self.assertEqual(result["units"], {"x": "u" * 32, "y": "V"})
        for x_unit, y_unit in (("u" * 33, "V"), ("°C", "u" * 33)):
            with self.subTest(x_unit=x_unit, y_unit=y_unit):
                with self.assertRaises(CalibrationError):
                    analyze_csv(PERFECT_CSV, "reference", "output", x_unit, y_unit)

    def test_units_are_labels_without_implicit_conversion(self):
        celsius = analyze_csv(PERFECT_CSV, "reference", "output", "°C", "V")
        kelvin = analyze_csv(PERFECT_CSV, "reference", "output", "K", "mV")
        self.assertEqual(celsius["fit"], kelvin["fit"])
        self.assertEqual(celsius["points"], kelvin["points"])

    def test_nonfinite_fit_parameters_fail_explicitly(self):
        # A finite input can imply a slope too large for IEEE-754 float.
        with self.assertRaises(CalibrationError):
            analyze_csv("x,y\n0,0\n1e-320,1\n2e-320,2\n", "x", "y")


class BundleTests(unittest.TestCase):
    def test_bundle_preserves_exact_source_hash_settings_and_reproduces_analysis(self):
        text = "\ufeffreference;output\r\n 0 ; 1 \r\n1;3\r\n2;5\r\n"
        bundle = create_bundle(text, "reference", "output", " °C ", " V ")
        self.assertEqual(bundle["schema_version"], 1)
        self.assertEqual(bundle["app_version"], APP_VERSION)
        self.assertEqual(bundle["source"]["csv_text"], text)
        self.assertEqual(bundle["source"]["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(bundle["settings"], {
            "x_column": "reference", "y_column": "output", "x_unit": "°C", "y_unit": "V",
        })
        rerun = analyze_csv(bundle["source"]["csv_text"], **bundle["settings"])
        self.assertEqual(bundle["analysis"], rerun)
        self.assertEqual(json.loads(json.dumps(bundle, ensure_ascii=False, allow_nan=False)), bundle)
        generated = datetime.datetime.fromisoformat(bundle["generated_at"].replace("Z", "+00:00"))
        self.assertEqual(generated.utcoffset(), datetime.timedelta(0))

    def test_identical_source_and_settings_yield_identical_analysis(self):
        first = create_bundle(PERFECT_CSV, "reference", "output")
        second = create_bundle(PERFECT_CSV, "reference", "output")
        for key in ("source", "settings", "analysis", "schema_version", "app_version"):
            self.assertEqual(first[key], second[key])

    def test_source_hash_distinguishes_line_endings_even_when_fit_is_same(self):
        unix = create_bundle(PERFECT_CSV, "reference", "output")
        windows = create_bundle(PERFECT_CSV.replace("\n", "\r\n"), "reference", "output")
        self.assertNotEqual(unix["source"]["sha256"], windows["source"]["sha256"])
        self.assertEqual(unix["analysis"]["fit"], windows["analysis"]["fit"])


if __name__ == "__main__":
    unittest.main()

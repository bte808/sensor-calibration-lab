"""Browser JSON spelling must not invalidate unchanged synthetic reports.

The numeric rewrite below is a specific observed JSON.stringify result, not a
general JavaScript serializer. The tests run with the Python standard library.
"""

from contextlib import redirect_stdout
import copy
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

import app
from calibration import create_bundle


PYTHON_FLOAT_TOKEN = "1.0000000000000001e18"
BROWSER_INTEGER_TOKEN = "1000000000000000100"

# Deterministic synthetic numbers, not observations from physical equipment.
# Keep the original CSV text intact throughout each browser/replay round trip.
LARGE_REFERENCE_CSV = (
    "reference,output\n"
    "1000000000000000100,0\n"
    "1000000000000000200,1\n"
    "1000000000000000400,2\n"
)
LARGE_OUTPUT_CSV = (
    "reference,output\n"
    "0,1000000000000000100\n"
    "1,1000000000000000200\n"
    "2,1000000000000000400\n"
)


class BrowserReplayTests(unittest.TestCase):
    def setUp(self):
        # Establish equivalence independently, before creating or reading any
        # report: both decimal spellings identify exactly the same binary64.
        self.target_float = float(PYTHON_FLOAT_TOKEN)
        self.browser_integer = int(BROWSER_INTEGER_TOKEN)
        self.assertEqual(self.target_float, float(BROWSER_INTEGER_TOKEN))
        self.assertEqual(self.target_float.as_integer_ratio(), float(BROWSER_INTEGER_TOKEN).as_integer_ratio())
        self.assertNotEqual(self.target_float, self.browser_integer)
        self.workspace = tempfile.TemporaryDirectory(prefix="calibration-browser-replay-")
        self.addCleanup(self.workspace.cleanup)
        self.directory = Path(self.workspace.name)
        self.report = self.directory / "synthetic-browser-report.json"
        self.output = self.directory / "reproduced.json"

    def make_bundle(self, text=LARGE_REFERENCE_CSV):
        bundle = create_bundle(text, "reference", "output", "a.u.", "a.u.")
        bundle["presentation"] = {
            "source_name": "synthetic-browser-number-spelling.csv",
            "synthetic_example": True,
        }
        return bundle

    def write_report(self, bundle, browser_spelling=False):
        replacements = []

        def rewrite(value):
            if type(value) is float and value == self.target_float:
                replacements.append(value)
                return self.browser_integer
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            return value

        serialized = rewrite(bundle) if browser_spelling else bundle
        if browser_spelling:
            self.assertTrue(replacements, "The fixture must exercise the observed browser rewrite")
        self.report.write_text(json.dumps(serialized, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        decoded = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(decoded["source"], bundle["source"])
        return decoded

    def replay(self):
        with redirect_stdout(io.StringIO()):
            app.replay_bundle(self.report, self.output)

    def assert_rejected(self, bundle):
        self.write_report(bundle)
        with self.assertRaisesRegex(ValueError, "分析结果与报告不一致"):
            self.replay()
        self.assertFalse(self.output.exists(), "Rejected reports must not create output")

    def assert_browser_report_replays(self, text, numeric_column):
        bundle = self.make_bundle(text)
        decoded = self.write_report(bundle, browser_spelling=True)
        self.assertIs(type(decoded["analysis"]["points"][0][numeric_column]), int)
        self.assertEqual(float(decoded["analysis"]["points"][0][numeric_column]), self.target_float)
        self.replay()
        reproduced = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(reproduced["source"]["csv_text"], text)
        self.assertEqual(reproduced["source"], bundle["source"])
        self.assertEqual(reproduced["analysis"], bundle["analysis"])

    def test_browser_spelling_replays_large_reference(self):
        self.assert_browser_report_replays(LARGE_REFERENCE_CSV, "x")

    def test_browser_spelling_replays_large_output(self):
        self.assert_browser_report_replays(LARGE_OUTPUT_CSV, "y")

    def test_one_representable_ulp_change_is_rejected(self):
        for text, column in ((LARGE_REFERENCE_CSV, "x"), (LARGE_OUTPUT_CSV, "y")):
            with self.subTest(column=column):
                bundle = self.make_bundle(text)
                expected = bundle["analysis"]["points"][0][column]
                changed = math.nextafter(expected, math.inf)
                self.assertEqual(changed - expected, math.ulp(expected))
                bundle["analysis"]["points"][0][column] = changed
                self.assert_rejected(bundle)

    def test_missing_or_extra_nested_keys_are_rejected(self):
        original = self.make_bundle()
        for operation in ("missing", "extra"):
            with self.subTest(operation=operation):
                bundle = copy.deepcopy(original)
                if operation == "missing":
                    del bundle["analysis"]["fit"]["intercept"]
                else:
                    bundle["analysis"]["fit"]["unexpected"] = 0
                self.assert_rejected(bundle)

    def test_boolean_count_is_rejected_even_when_equal_to_zero(self):
        bundle = self.make_bundle()
        self.assertEqual(bundle["analysis"]["counts"]["excluded"], 0)
        bundle["analysis"]["counts"]["excluded"] = False
        self.assert_rejected(bundle)

    def test_float_count_is_rejected_even_when_numerically_equal(self):
        bundle = self.make_bundle()
        bundle["analysis"]["counts"]["valid"] = float(bundle["analysis"]["counts"]["valid"])
        self.assert_rejected(bundle)

    def test_boolean_cannot_replace_a_float_result(self):
        bundle = self.make_bundle()
        self.assertEqual(bundle["analysis"]["points"][0]["residual_y"], 0.0)
        bundle["analysis"]["points"][0]["residual_y"] = False
        self.assert_rejected(bundle)

    def test_integer_too_large_for_binary64_is_cleanly_rejected(self):
        bundle = self.make_bundle()
        bundle["analysis"]["fit"]["slope"] = 10 ** 400
        self.assert_rejected(bundle)

    def test_missing_list_item_is_rejected(self):
        bundle = self.make_bundle()
        bundle["analysis"]["points"].pop()
        self.assert_rejected(bundle)


if __name__ == "__main__":
    unittest.main()

"""Actionable, bounded diagnostics for insufficient valid calibration data."""

import unittest

from calibration import CalibrationError, analyze_csv


class ImportErrorTests(unittest.TestCase):
    def error_message(self, text, x_column="reference", y_column="output"):
        with self.assertRaises(CalibrationError) as caught:
            analyze_csv(text, x_column, y_column)
        return str(caught.exception)

    def test_decimal_comma_failure_identifies_lines_column_and_recovery(self):
        message = self.error_message("reference;output\n0;0,5\n10;0,7\n20;0,9\n")
        self.assertTrue(message.startswith("至少需要 3 对有效数值；当前有 0 对，排除了 3 条记录。"))
        for line in (2, 3, 4):
            self.assertIn("CSV 第 {} 行（记录结束行）".format(line), message)
        self.assertEqual(message.count("“output”不是可解析的数值"), 3)
        self.assertIn("点号小数（如 0.5）", message)
        self.assertIn("单位请在单位栏单独填写", message)
        self.assertNotIn("另有", message)

    def test_quoted_multiline_record_uses_physical_end_line(self):
        text = 'reference,output,note\n0,bad,"first\nsecond"\n1,2,ok\n2,,ok\n'
        message = self.error_message(text)
        self.assertTrue(message.startswith("至少需要 3 对有效数值；当前有 1 对，排除了 2 条记录。"))
        self.assertIn("CSV 第 3 行（记录结束行）：“output”不是可解析的数值", message)
        self.assertIn("CSV 第 5 行（记录结束行）：“output”缺少数值", message)
        self.assertNotIn("CSV 第 2 行", message)

    def test_only_first_three_exclusions_are_shown_with_remaining_count(self):
        text = "reference,output\n" + "".join("{},invalid\n".format(i) for i in range(20))
        message = self.error_message(text)
        self.assertIn("当前有 0 对，排除了 20 条记录", message)
        self.assertEqual(message.count("CSV 第 "), 3)
        for line in (2, 3, 4):
            self.assertIn("CSV 第 {} 行".format(line), message)
        self.assertNotIn("CSV 第 5 行", message)
        self.assertIn("另有 17 条排除记录未显示", message)
        self.assertLess(len(message), 600)

    def test_long_column_names_do_not_make_an_unbounded_message(self):
        column = "synthetic_output_" + "x" * 600
        text = "reference,{}\n0,invalid\n1,invalid\n2,invalid\n".format(column)
        message = self.error_message(text, y_column=column)
        self.assertEqual(message.count("CSV 第 "), 3)
        self.assertEqual(message.count("过长信息已截断"), 3)
        self.assertIn("synthetic_output_", message)
        self.assertIn("不是可解析的数值", message)
        self.assertLess(len(message), 1100)

    def test_valid_but_insufficient_data_says_how_many_pairs_to_add(self):
        for count in (1, 2):
            with self.subTest(count=count):
                text = "reference,output\n" + "".join("{},{}\n".format(i, i + 1) for i in range(count))
                message = self.error_message(text)
                self.assertTrue(message.startswith("至少需要 3 对有效数值；当前有 {} 对，排除了 0 条记录。".format(count)))
                self.assertIn("请再增加 {} 对参考量与传感器输出观测".format(3 - count), message)
                self.assertNotIn("排除记录：", message)

    def test_specific_reasons_for_both_selected_columns_are_preserved(self):
        message = self.error_message("reference,output\nnan,1e101\n,wrong\n2,0.6V\n")
        for reason in (
            "“reference”不是有限数值",
            "“output”的绝对值超过 1e100",
            "“reference”缺少数值",
            "“output”不是可解析的数值",
        ):
            self.assertIn(reason, message)

    def test_decimal_comma_and_unit_suffix_are_not_silently_converted(self):
        text = "reference;output\n0;0.5\n1;1.5\n2;2.5\n3;3,5\n4;4.5 V\n"
        result = analyze_csv(text, "reference", "output")
        self.assertEqual(result["counts"], {"total": 5, "valid": 3, "excluded": 2})
        self.assertEqual([row["source_line"] for row in result["excluded_rows"]], [5, 6])
        self.assertEqual(result["fit"]["slope"], 1.0)
        self.assertEqual(result["fit"]["intercept"], 0.5)
        self.assertTrue(all(row["residual_y"] == 0.0 for row in result["points"]))


if __name__ == "__main__":
    unittest.main()

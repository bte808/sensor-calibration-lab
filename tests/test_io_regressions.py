"""CLI encoding and all-or-nothing replay export regressions."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import app
from calibration.core import APP_VERSION, create_bundle


ROOT = Path(__file__).resolve().parents[1]
CSV_TEXT = "参考温度,输出\n0,1\n1,3\n2,5\n"


class ReplayExportTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.directory = Path(self.workspace.name)
        self.source = self.directory / "source.json"
        self.output = self.directory / "result.json"
        self.bundle = create_bundle(CSV_TEXT, "参考温度", "输出", "°C", "V")
        self.bundle["presentation"] = {"source_name": "合成示例.csv", "synthetic_example": True}
        self.save_source()

    def save_source(self):
        # ASCII JSON can legitimately encode an unpaired surrogate, although
        # that decoded string cannot be encoded as a UTF-8 output document.
        self.source.write_text(json.dumps(self.bundle, ensure_ascii=True), encoding="utf-8")

    def replay(self, output=None):
        with redirect_stdout(io.StringIO()):
            app.replay_bundle(self.source, self.output if output is None else output)

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.directory.glob(".calibration-*.tmp")), [])

    def test_valid_export_is_complete_utf8_and_leaves_no_temporary_file(self):
        self.replay()
        raw = self.output.read_bytes()
        self.assertIn("合成示例.csv".encode("utf-8"), raw)
        result = json.loads(raw.decode("utf-8"))
        self.assertEqual(result["analysis"], self.bundle["analysis"])
        self.assertEqual(result["presentation"], self.bundle["presentation"])
        self.assert_no_temporary_files()

    def test_invalid_unicode_creates_no_output_and_retry_can_succeed(self):
        self.bundle["presentation"]["source_name"] = "bad\ud800name.csv"
        self.save_source()
        with self.assertRaisesRegex(ValueError, "Unicode"):
            self.replay()
        self.assertFalse(self.output.exists())
        self.assert_no_temporary_files()
        self.bundle["presentation"]["source_name"] = "合成示例.csv"
        self.save_source()
        self.replay()
        self.assertEqual(json.loads(self.output.read_bytes())["presentation"], self.bundle["presentation"])

    def test_invalid_unicode_does_not_touch_existing_output(self):
        self.output.write_bytes(b"existing data")
        self.bundle["presentation"]["source_name"] = "bad\ud800name.csv"
        self.save_source()
        with self.assertRaisesRegex(ValueError, "Unicode"):
            self.replay()
        self.assertEqual(self.output.read_bytes(), b"existing data")
        self.assert_no_temporary_files()

    def test_existing_output_is_not_overwritten(self):
        self.output.write_bytes(b"existing data")
        with self.assertRaises(FileExistsError):
            self.replay()
        self.assertEqual(self.output.read_bytes(), b"existing data")
        self.assert_no_temporary_files()

    def test_source_cannot_be_overwritten(self):
        original = self.source.read_bytes()
        with self.assertRaises(FileExistsError):
            self.replay(self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assert_no_temporary_files()

    def test_flush_failure_creates_no_output(self):
        with patch.object(app.os, "fsync", side_effect=OSError("disk sync failed")):
            with self.assertRaisesRegex(OSError, "disk sync failed"):
                self.replay()
        self.assertFalse(self.output.exists())
        self.assert_no_temporary_files()

    def test_commit_failure_creates_no_output(self):
        with patch.object(app, "_commit_new_file", side_effect=OSError("publication failed")):
            with self.assertRaisesRegex(OSError, "publication failed"):
                self.replay()
        self.assertFalse(self.output.exists())
        self.assert_no_temporary_files()

    def test_concurrent_destination_is_not_overwritten(self):
        commit = app._commit_new_file

        def other_writer_wins(temporary, target):
            target.write_bytes(b"another writer")
            commit(temporary, target)

        with patch.object(app, "_commit_new_file", side_effect=other_writer_wins):
            with self.assertRaises(FileExistsError):
                self.replay()
        self.assertEqual(self.output.read_bytes(), b"another writer")
        self.assert_no_temporary_files()

    def test_cleanup_failure_preserves_successful_complete_export(self):
        errors = io.StringIO()
        with patch.object(Path, "unlink", side_effect=OSError("cleanup denied")), redirect_stderr(errors):
            self.replay()
        self.assertEqual(json.loads(self.output.read_bytes())["analysis"], self.bundle["analysis"])
        self.assertIn("Warning:", errors.getvalue())
        self.assertIn("cleanup denied", errors.getvalue())

    def test_cleanup_failure_does_not_mask_original_error(self):
        errors = io.StringIO()
        with patch.object(app, "_commit_new_file", side_effect=OSError("original failure")):
            with patch.object(Path, "unlink", side_effect=OSError("cleanup denied")), redirect_stderr(errors):
                with self.assertRaisesRegex(OSError, "original failure"):
                    self.replay()
        self.assertFalse(self.output.exists())
        self.assertIn("cleanup denied", errors.getvalue())

    def test_previous_version_report_is_recomputed_with_current_version(self):
        self.bundle["app_version"] = "1.0.0"
        self.bundle["analysis"]["metrics"]["rmse_y"] = 123.0
        self.save_source()
        self.replay()
        result = json.loads(self.output.read_bytes())
        self.assertEqual(result["app_version"], APP_VERSION)
        self.assertEqual(result["analysis"]["metrics"]["rmse_y"], 0.0)


class ConsoleEncodingTests(unittest.TestCase):
    def run_cli(self, encoding, script, *arguments):
        environment = os.environ.copy()
        # Override CI's UTF-8 default and test redirected legacy-codepage output.
        environment.update(PYTHONUTF8="0", PYTHONIOENCODING=encoding + ":strict")
        return subprocess.run(
            [sys.executable, str(ROOT / script), *map(str, arguments)],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )

    def test_help_does_not_crash_with_ascii_or_cp1252_output(self):
        for encoding in ("ascii", "cp1252"):
            with self.subTest(encoding=encoding):
                result = self.run_cli(encoding, "app.py", "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b"--replay", result.stdout)
                self.assertIn(b"\\u", result.stdout)
                self.assertEqual(result.stderr, b"")

    def test_replay_success_keeps_report_utf8_with_legacy_console(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.json", Path(directory) / "result.json"
            bundle = create_bundle(CSV_TEXT, "参考温度", "输出", "°C", "V")
            source.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            for encoding in ("ascii", "cp1252"):
                with self.subTest(encoding=encoding):
                    result = self.run_cli(encoding, "app.py", "--replay", source, "--output", output)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(b"SHA-256", result.stdout)
                    self.assertEqual(json.loads(output.read_bytes())["source"]["csv_text"], CSV_TEXT)
                    self.assertIn("参考温度".encode("utf-8"), output.read_bytes())
                    output.unlink()

    def test_replay_error_is_actionable_without_unicode_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text("{}", encoding="utf-8")
            for encoding in ("ascii", "cp1252"):
                with self.subTest(encoding=encoding):
                    result = self.run_cli(encoding, "app.py", "--replay", source)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("复算失败".encode(encoding, errors="backslashreplace"), result.stderr)
                    self.assertNotIn(b"Traceback", result.stderr)
                    self.assertNotIn(b"UnicodeEncodeError", result.stderr)

    def test_synthetic_generator_check_supports_legacy_console(self):
        for encoding in ("ascii", "cp1252"):
            with self.subTest(encoding=encoding):
                result = self.run_cli(encoding, "examples/generate_example.py", "--check")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b"18", result.stdout)
                self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()

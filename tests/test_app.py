"""Loopback HTTP and CLI integration tests, with no external network access."""

import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import app
from calibration.core import APP_VERSION, analyze_csv, create_bundle


CSV_TEXT = "参考温度,传感器输出\n0,1\n1,3\n2,5\n"
SETTINGS = {"x_column": "参考温度", "y_column": "传感器输出", "x_unit": "°C", "y_unit": "V"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LocalHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ephemeral loopback port avoids collisions with an already running app.
        cls.server = app.CalibrationServer(("127.0.0.1", 0), app.Handler)
        cls.worker = threading.Thread(
            target=cls.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            name="calibration-test-http",
            daemon=True,
        )
        cls.worker.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=2)
        if cls.worker.is_alive():
            raise AssertionError("Local HTTP test server did not stop")

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        request_headers = dict(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def assert_json_error(self, response, expected_status):
        status, headers, raw = response
        self.assertEqual(status, expected_status)
        self.assertIn("application/json", headers["Content-Type"])
        message = json.loads(raw.decode("utf-8"))
        self.assertIsInstance(message.get("error"), str)
        self.assertTrue(message["error"])
        return message

    def test_health_and_security_headers(self):
        status, headers, raw = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"status": "ok", "version": APP_VERSION})
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_only_declared_static_files_are_served(self):
        for path, filename, media_type in (
            ("/", "index.html", "text/html"),
            ("/app.js", "app.js", "text/javascript"),
            ("/style.css", "style.css", "text/css"),
        ):
            with self.subTest(path=path):
                status, headers, raw = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(media_type, headers["Content-Type"])
                self.assertEqual(raw, (PROJECT_ROOT / "web" / filename).read_bytes())
        for path in ("/app.py", "/README.md", "/../app.py", "/%2e%2e/app.py", "/web/index.html", "/.git/config", "/missing"):
            with self.subTest(path=path):
                self.assert_json_error(self.request("GET", path), 404)

    def test_inspect_and_analyze_preserve_utf8_and_reproduce_bundle(self):
        inspect_status, _, inspected_raw = self.request("POST", "/api/inspect", {"csv_text": CSV_TEXT})
        self.assertEqual(inspect_status, 200)
        inspected = json.loads(inspected_raw)
        self.assertEqual(inspected["headers"], ["参考温度", "传感器输出"])
        self.assertEqual(inspected["row_count"], 3)
        status, headers, raw = self.request("POST", "/api/analyze", {"csv_text": CSV_TEXT, **SETTINGS})
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", headers["Content-Type"])
        self.assertIn("参考温度".encode("utf-8"), raw)
        bundle = json.loads(raw.decode("utf-8"))
        self.assertEqual(bundle["source"]["csv_text"], CSV_TEXT)
        self.assertEqual(bundle["source"]["sha256"], hashlib.sha256(CSV_TEXT.encode("utf-8")).hexdigest())
        self.assertEqual(bundle["settings"], SETTINGS)
        self.assertEqual(bundle["analysis"], analyze_csv(bundle["source"]["csv_text"], **bundle["settings"]))
        self.assertAlmostEqual(bundle["analysis"]["fit"]["slope"], 2)

    def test_synthetic_example_is_marked_and_analyzable(self):
        status, _, raw = self.request("GET", "/api/example")
        self.assertEqual(status, 200)
        example = json.loads(raw)
        self.assertIs(example["synthetic"], True)
        self.assertEqual(example["name"], "synthetic_temperature.csv")
        self.assertEqual(example["csv_text"], (PROJECT_ROOT / "examples" / example["name"]).read_text(encoding="utf-8"))
        status, _, raw = self.request("POST", "/api/analyze", {
            "csv_text": example["csv_text"],
            "x_column": "reference_temperature_c", "y_column": "sensor_output_v",
            "x_unit": "°C", "y_unit": "V",
        })
        self.assertEqual(status, 200)
        analysis = json.loads(raw)["analysis"]
        self.assertEqual(analysis["counts"], {"total": 18, "valid": 18, "excluded": 0})
        self.assertEqual(analysis["repeatability"]["repeated_groups"], 6)
        self.assertEqual(analysis["repeatability"]["degrees_of_freedom"], 12)

    def test_insufficient_valid_pairs_report_csv_locations_and_recovery(self):
        response = self.request("POST", "/api/analyze", {
            "csv_text": "reference;output\n0;0,5\n10;0,7\n20;0,9\n",
            "x_column": "reference", "y_column": "output",
        })
        message = self.assert_json_error(response, 400)["error"]
        self.assertIn("当前有 0 对，排除了 3 条记录", message)
        for line in (2, 3, 4):
            self.assertIn("CSV 第 {} 行".format(line), message)
        self.assertIn("“output”不是可解析的数值", message)
        self.assertIn("点号小数（如 0.5）", message)

    def test_exact_loopback_origin_is_accepted(self):
        origin = "http://127.0.0.1:{}".format(self.port)
        status, _, _ = self.request("POST", "/api/inspect", {"csv_text": CSV_TEXT}, {"Origin": origin})
        self.assertEqual(status, 200)

    def test_wrong_host_or_origin_is_rejected_for_reads_and_writes(self):
        invalid_headers = (
            {"Host": "example.invalid"},
            {"Host": "127.0.0.1:{}".format(self.port + 1)},
            {"Host": "localhost:{}".format(self.port)},
            {"Origin": "https://example.invalid"},
            {"Origin": "null"},
            {"Origin": "https://127.0.0.1:{}".format(self.port)},
            {"Origin": "http://127.0.0.1:{}".format(self.port + 1)},
        )
        for headers in invalid_headers:
            for method, path, body in (("GET", "/api/health", None), ("POST", "/api/inspect", {"csv_text": CSV_TEXT})):
                with self.subTest(headers=headers, method=method):
                    self.assert_json_error(self.request(method, path, body, headers), 403)

    def test_unknown_post_route_is_rejected(self):
        self.assert_json_error(self.request("POST", "/api/missing", {"csv_text": CSV_TEXT}), 404)

    def test_invalid_api_parameters_produce_json_errors(self):
        payloads = (
            {}, {"csv_text": None}, {"csv_text": 17},
            {"csv_text": CSV_TEXT},
            {"csv_text": CSV_TEXT, **SETTINGS, "x_column": "unknown"},
            {"csv_text": CSV_TEXT, **SETTINGS, "y_column": SETTINGS["x_column"]},
            {"csv_text": CSV_TEXT, **SETTINGS, "x_column": []},
            {"csv_text": CSV_TEXT, **SETTINGS, "y_unit": 123},
            {"csv_text": CSV_TEXT, **SETTINGS, "x_unit": "u" * 33},
            {"csv_text": "x,y\n0,1\n1,2\n", "x_column": "x", "y_column": "y"},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assert_json_error(self.request("POST", "/api/analyze", payload), 400)

    def test_bad_json_and_utf8_are_rejected(self):
        for body in (b"{", b"null", b"[]", b'"text"', b'{"csv_text":"\xff"}'):
            with self.subTest(body=body):
                self.assert_json_error(self.request("POST", "/api/inspect", body, {"Content-Type": "application/json"}), 400)

    def test_wrong_content_type_is_rejected(self):
        self.assert_json_error(self.request("POST", "/api/inspect", b"{}", {"Content-Type": "text/plain"}), 415)

    def test_oversized_declared_request_is_rejected_before_reading(self):
        # No large upload is sent: the server must reject the length immediately.
        self.assert_json_error(self.request("POST", "/api/inspect", b"", {
            "Content-Type": "application/json", "Content-Length": str(app.MAX_REQUEST_BYTES + 1),
        }), 413)

    def test_empty_negative_and_invalid_content_lengths_are_rejected(self):
        for length, status in (("0", 413), ("-1", 413), ("invalid", 400)):
            with self.subTest(length=length):
                self.assert_json_error(self.request("POST", "/api/inspect", b"", {
                    "Content-Type": "application/json", "Content-Length": length,
                }), status)


class ReplayCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="calibration-test-")
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.original = create_bundle(CSV_TEXT, **SETTINGS)
        self.report = self.directory / "synthetic report.json"
        self.write_bundle(self.original)

    def write_bundle(self, value):
        self.report.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *arguments):
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "app.py"), *map(str, arguments)],
            cwd=str(self.directory), env=environment, capture_output=True,
            text=True, encoding="utf-8", timeout=10,
        )

    def assert_cli_failure(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("复算失败", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_replay_without_output_is_read_only(self):
        before = self.report.read_bytes()
        result = self.run_cli("--replay", self.report)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("复算成功", result.stdout)
        self.assertIn("SHA-256", result.stdout)
        self.assertEqual(self.report.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.report])

    def test_replay_writes_new_reproducible_json(self):
        output = self.directory / "recomputed report.json"
        result = self.run_cli("--replay", self.report, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        reproduced = json.loads(output.read_text(encoding="utf-8"))
        for key in ("source", "settings", "analysis", "schema_version", "app_version"):
            self.assertEqual(reproduced[key], self.original[key])
        self.assertEqual(reproduced["analysis"], analyze_csv(reproduced["source"]["csv_text"], **reproduced["settings"]))

    def test_replay_preserves_synthetic_presentation_in_new_output(self):
        marked = copy.deepcopy(self.original)
        marked["presentation"] = {
            "source_name": "合成示例 synthetic_temperature.csv",
            "synthetic_example": True,
        }
        self.write_bundle(marked)
        before = self.report.read_bytes()
        output = self.directory / "recomputed-synthetic.json"
        result = self.run_cli("--replay", self.report, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        reproduced = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(reproduced["presentation"], marked["presentation"])
        self.assertIs(reproduced["presentation"]["synthetic_example"], True)
        self.assertEqual(reproduced["analysis"], marked["analysis"])
        self.assertEqual(self.report.read_bytes(), before)

    def test_invalid_presentation_is_rejected_without_writing_output(self):
        invalid_presentations = (
            None, [], {},
            {"source_name": 123, "synthetic_example": True},
            {"source_name": "synthetic.csv", "synthetic_example": "true"},
            {"source_name": "synthetic.csv", "synthetic_example": 1},
            {"source_name": "synthetic.csv"},
            {"synthetic_example": True},
        )
        for presentation in invalid_presentations:
            with self.subTest(presentation=presentation):
                malformed = copy.deepcopy(self.original)
                malformed["presentation"] = presentation
                self.write_bundle(malformed)
                before = self.report.read_bytes()
                output = self.directory / "must-not-create.json"
                result = self.run_cli("--replay", self.report, "--output", output)
                self.assert_cli_failure(result)
                self.assertFalse(output.exists())
                self.assertEqual(self.report.read_bytes(), before)

    def test_modified_source_is_rejected_without_creating_output(self):
        modified = copy.deepcopy(self.original)
        modified["source"]["csv_text"] += "3,7\n"
        self.write_bundle(modified)
        output = self.directory / "must-not-exist.json"
        result = self.run_cli("--replay", self.report, "--output", output)
        self.assert_cli_failure(result)
        self.assertIn("指纹", result.stderr)
        self.assertFalse(output.exists())

    def test_modified_same_version_analysis_is_rejected(self):
        modified = copy.deepcopy(self.original)
        modified["analysis"]["fit"]["slope"] = 999
        self.write_bundle(modified)
        result = self.run_cli("--replay", self.report)
        self.assert_cli_failure(result)
        self.assertIn("不一致", result.stderr)

    def test_modified_settings_cannot_silently_keep_old_analysis(self):
        modified = copy.deepcopy(self.original)
        modified["settings"]["y_unit"] = "mV"
        self.write_bundle(modified)
        self.assert_cli_failure(self.run_cli("--replay", self.report))

    def test_replay_refuses_to_overwrite_existing_file_or_its_input(self):
        existing = self.directory / "existing.json"
        existing.write_bytes(b"keep this existing output\n")
        for output in (existing, self.report):
            with self.subTest(output=output.name):
                before = output.read_bytes()
                self.assert_cli_failure(self.run_cli("--replay", self.report, "--output", output))
                self.assertEqual(output.read_bytes(), before)

    def test_different_version_recomputes_and_warns_about_comparison(self):
        modified = copy.deepcopy(self.original)
        modified["app_version"] = "0.0.0-synthetic-test"
        modified["analysis"]["fit"]["slope"] = 999
        self.write_bundle(modified)
        output = self.directory / "new-version.json"
        result = self.run_cli("--replay", self.report, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("版本不同", result.stdout)
        reproduced = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(reproduced["app_version"], APP_VERSION)
        self.assertAlmostEqual(reproduced["analysis"]["fit"]["slope"], 2)

    def test_malformed_bundles_have_concise_errors(self):
        wrong_source_type = copy.deepcopy(self.original)
        wrong_source_type["source"]["csv_text"] = 123
        for bundle in ([], {}, {"schema_version": 99}, {"schema_version": 1}, wrong_source_type):
            with self.subTest(bundle=bundle):
                self.write_bundle(bundle)
                self.assert_cli_failure(self.run_cli("--replay", self.report))

    def test_invalid_json_and_missing_file_have_concise_errors(self):
        self.report.write_text("{", encoding="utf-8")
        self.assert_cli_failure(self.run_cli("--replay", self.report))
        self.assert_cli_failure(self.run_cli("--replay", self.directory / "missing.json"))

    def test_output_without_replay_and_invalid_port_are_rejected(self):
        for arguments in (("--output", self.directory / "output.json"), ("--port", "0"), ("--port", "65536")):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.directory / "output.json").exists())


if __name__ == "__main__":
    unittest.main()

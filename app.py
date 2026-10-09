#!/usr/bin/env python3
"""Zero-dependency, loopback-only sensor calibration workbench."""
import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from calibration.core import APP_VERSION, CalibrationError, create_bundle, inspect_csv

ROOT = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class CalibrationServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "SensorCalibrationLab/" + APP_VERSION

    def _send(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value, status=200):
        self._send(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _local_request(self):
        # Reject other websites and DNS rebinding; no CORS access is enabled.
        expected = "127.0.0.1:" + str(self.server.server_port)
        if self.headers.get("Host") != expected:
            self._json({"error": "请使用启动时显示的 127.0.0.1 本地地址。"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + expected:
            self._json({"error": "只接受本地页面发起的请求。"}, 403)
            return False
        return True

    def do_GET(self):
        if not self._local_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json({"status": "ok", "version": APP_VERSION})
        elif path == "/api/example":
            self._json({"name": "synthetic_temperature.csv", "synthetic": True, "csv_text": (ROOT / "examples" / "synthetic_temperature.csv").read_text(encoding="utf-8")})
        else:
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path not in static:
                self._json({"error": "未找到页面。"}, 404)
                return
            filename, content_type = static[path]
            self._send((ROOT / "web" / filename).read_bytes(), content_type)

    def do_POST(self):
        if not self._local_request():
            return
        if self.path not in ("/api/inspect", "/api/analyze"):
            self._json({"error": "未找到接口。"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "请求长度无效。"}, 400)
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json({"error": "请求为空或超过 2 MiB。"}, 413)
            return
        if self.headers.get_content_type() != "application/json":
            self._json({"error": "请发送 JSON 请求。"}, 415)
            return
        try:
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise CalibrationError("请求应为 JSON 对象。")
            text = body.get("csv_text")
            if not isinstance(text, str):
                raise CalibrationError("csv_text 必须是文本。")
            if self.path == "/api/inspect":
                self._json(inspect_csv(text))
            else:
                settings = {key: body.get(key, "") for key in ("x_column", "y_column", "x_unit", "y_unit")}
                if not all(isinstance(value, str) for value in settings.values()):
                    raise CalibrationError("列名和单位必须是文本。")
                self._json(create_bundle(text, **settings))
        except (CalibrationError, UnicodeError, ValueError) as error:
            self._json({"error": str(error)}, 400)
        except (TimeoutError, OSError):
            self.close_connection = True

    def log_message(self, format_string, *args):
        # Request paths and uploaded data are intentionally not logged.
        pass


def replay_bundle(path, output):
    """Verify source integrity and reproduce analysis from an exported bundle."""
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
        raise ValueError("不支持的报告格式。")
    source = bundle["source"]
    if not isinstance(source, dict) or not isinstance(source.get("csv_text"), str) or not isinstance(source.get("sha256"), str):
        raise ValueError("报告必须包含文本形式的 CSV 和 SHA-256。")
    text = source["csv_text"]
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != source["sha256"]:
        raise ValueError("CSV 指纹不一致，报告源数据已被修改。")
    settings = bundle["settings"]
    if not isinstance(settings, dict):
        raise ValueError("报告设置必须是 JSON 对象。")
    reproduced = create_bundle(text, **{key: settings[key] for key in ("x_column", "y_column", "x_unit", "y_unit")})
    if bundle.get("app_version") == APP_VERSION and reproduced["analysis"] != bundle.get("analysis"):
        raise ValueError("重新计算的分析结果与报告不一致。")
    if "presentation" in bundle:
        presentation = bundle["presentation"]
        if not isinstance(presentation, dict) or not isinstance(presentation.get("source_name"), str) or not isinstance(presentation.get("synthetic_example"), bool):
            raise ValueError("报告的来源说明格式无效。")
        reproduced["presentation"] = {
            "source_name": presentation["source_name"],
            "synthetic_example": presentation["synthetic_example"],
        }
    if output:
        with Path(output).open("x", encoding="utf-8") as target:
            json.dump(reproduced, target, ensure_ascii=False, allow_nan=False, indent=2)
            target.write("\n")
    print("复算成功；源数据 SHA-256 已核对。" + (" 结果已另存。" if output else ""))
    if bundle.get("app_version") != APP_VERSION:
        print("提示：原报告版本不同，已重新计算，但未要求与旧版结果完全一致。")


def main():
    parser = argparse.ArgumentParser(description="传感器标定实验台（仅本地运行）")
    parser.add_argument("--port", type=int, default=8765, help="本地端口，默认 8765")
    parser.add_argument("--replay", metavar="REPORT.json", help="核对并复算已导出的 JSON 报告")
    parser.add_argument("--output", metavar="NEW.json", help="将复算结果写入一个新文件")
    args = parser.parse_args()
    if args.replay:
        try:
            replay_bundle(args.replay, args.output)
        except (ValueError, OSError, KeyError, TypeError) as error:
            parser.exit(1, "复算失败：" + str(error) + "\n")
        return
    if args.output:
        parser.error("--output 需与 --replay 一起使用。")
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1–65535 之间。")
    try:
        server = CalibrationServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        parser.exit(1, "无法启动本地服务：" + str(error) + "。可用 --port 选择另一端口。\n")
    print("传感器标定实验台 http://127.0.0.1:" + str(args.port), flush=True)
    print("仅本机访问；数据不写入服务端文件。按 Ctrl+C 退出。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

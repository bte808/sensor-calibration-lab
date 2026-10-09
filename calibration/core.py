"""CSV parsing and forward linear sensor calibration without third-party packages.

The fitted model is output = slope * reference + intercept. Reference values are
treated as fixed. Repeatability describes observed within-level variation; it is
neither an uncertainty budget nor evidence of measurement accuracy.
"""

import csv
import hashlib
import io
import math
from datetime import datetime, timezone
from typing import Dict, List, Tuple


APP_VERSION = "1.0.0"
MAX_ROWS = 20_000
MAX_COLUMNS = 50
MAX_MAGNITUDE = 1e100
DELIMITERS = ",\t;"


class CalibrationError(ValueError):
    """A user-correctable input or numerical error."""


def _is_blank(row: List[str]) -> bool:
    # Delimiter-only records have fields and must remain visible as missing data.
    return not row or (len(row) == 1 and not row[0].strip())


def _detect_delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:65_536], delimiters=DELIMITERS).delimiter
    except csv.Error:
        # Sniffer can fail on inconsistent row widths; choose from the header so
        # that the real parser can produce an actionable row-width error.
        widths = []
        for delimiter in DELIMITERS:
            try:
                reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
                header = next((row for row in reader if not _is_blank(row)), [])
                widths.append((len(header), delimiter))
            except csv.Error:
                widths.append((0, delimiter))
        # Keep the first candidate (comma) in a tie, including a single column.
        return max(widths, key=lambda candidate: candidate[0])[1]


def _parse_csv(text: str) -> Tuple[List[str], List[Tuple[int, List[str]]], str]:
    if not isinstance(text, str):
        raise CalibrationError("CSV 内容必须是文本。")
    csv_text = text[1:] if text.startswith("\ufeff") else text
    if not csv_text.strip():
        raise CalibrationError("CSV 内容为空，请导入含表头和数据的文件。")
    delimiter = _detect_delimiter(csv_text)
    reader = csv.reader(io.StringIO(csv_text, newline=""), delimiter=delimiter, strict=True)
    headers = None
    records = []
    try:
        for row in reader:
            if _is_blank(row):
                continue
            if headers is None:
                headers = [cell.strip() for cell in row]
                if len(headers) > MAX_COLUMNS:
                    raise CalibrationError("CSV 最多支持 50 列。")
                if any(not header for header in headers):
                    raise CalibrationError("CSV 表头不能有空列名。")
                if len(set(headers)) != len(headers):
                    raise CalibrationError("CSV 表头不能有重复列名（忽略列名两端空白）。")
                continue
            if len(row) != len(headers):
                raise CalibrationError(
                    "CSV 第 {} 行结束的记录有 {} 列，表头有 {} 列，请检查分隔符和引号。".format(
                        reader.line_num, len(row), len(headers)
                    )
                )
            records.append((reader.line_num, row))
            if len(records) > MAX_ROWS:
                raise CalibrationError("CSV 最多支持 20,000 条数据记录。")
    except csv.Error as exc:
        raise CalibrationError(
            "无法解析 CSV（第 {} 行附近），请检查字段长度和引号是否完整。".format(reader.line_num)
        ) from exc
    if headers is None:
        raise CalibrationError("CSV 缺少表头。")
    if not records:
        raise CalibrationError("CSV 只有表头，没有数据记录。")
    return headers, records, delimiter


def inspect_csv(text: str) -> dict:
    """Validate CSV structure and return the first eight data records."""
    headers, records, delimiter = _parse_csv(text)
    return {
        "headers": headers,
        "preview": [row for _, row in records[:8]],
        "row_count": len(records),
        "delimiter": delimiter,
    }


def _unit(value: str) -> str:
    if not isinstance(value, str):
        raise CalibrationError("单位必须是文本。")
    value = value.strip()
    if len(value) > 32:
        raise CalibrationError("单位最多 32 个字符；单位仅作标注，不会自动换算。")
    return value


def _number(value: str, column: str) -> Tuple[float, str]:
    if not value.strip():
        return 0.0, "“{}”缺少数值".format(column)
    try:
        number = float(value.strip())
    except ValueError:
        return 0.0, "“{}”不是可解析的数值".format(column)
    if not math.isfinite(number):
        return 0.0, "“{}”不是有限数值".format(column)
    if abs(number) > MAX_MAGNITUDE:
        return 0.0, "“{}”的绝对值超过 1e100".format(column)
    return number, ""


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise CalibrationError("计算结果超出浮点数范围，请调整数据的数量级或单位后重试。")
    return value


def _center(values: List[float]) -> Tuple[float, List[float], float]:
    """Return mean, normalized centered values, and their coordinate scale.

    Center in normalized coordinates rather than subtracting a rounded mean.
    This preserves small spans around large offsets, even when the mean itself
    falls between two representable input floats. Constant decimals stay exact.
    """
    anchor = values[0]
    offsets = [value - anchor for value in values]
    scale = max(map(abs, offsets))
    if scale == 0:
        return anchor, [0.0] * len(values), 0.0
    normalized = [value / scale for value in offsets]
    average = math.fsum(normalized) / len(values)
    mean = math.fsum((anchor, average * scale))
    return mean, [value - average for value in normalized], scale


def _scaled_ratio(factor: float, numerator: float, denominator: float) -> float:
    """Evaluate factor * numerator / denominator without intermediate overflow."""
    if factor == 0 or numerator == 0:
        return 0.0
    fm, fe = math.frexp(factor)
    nm, ne = math.frexp(numerator)
    dm, de = math.frexp(denominator)
    try:
        result = math.ldexp(fm * nm / dm, fe + ne - de)
    except OverflowError as exc:
        raise CalibrationError("拟合斜率超出浮点数范围，请调整参考量或输出的单位后重试。") from exc
    return _finite(result)


def _rms(values: List[float], denominator: int) -> float:
    """Square, sum, and root in normalized coordinates to avoid underflow."""
    scale = max(abs(value) for value in values)
    if scale == 0:
        return 0.0
    result = scale * math.sqrt(math.fsum((value / scale) ** 2 for value in values) / denominator)
    return _finite(result)


def analyze_csv(
    text: str, x_column: str, y_column: str, x_unit: str = "", y_unit: str = ""
) -> dict:
    """Fit forward OLS and report residuals and within-reference repeatability."""
    headers, records, _ = _parse_csv(text)
    if not isinstance(x_column, str) or x_column not in headers:
        raise CalibrationError("请选择 CSV 中存在的参考量列。")
    if not isinstance(y_column, str) or y_column not in headers:
        raise CalibrationError("请选择 CSV 中存在的传感器输出列。")
    if x_column == y_column:
        raise CalibrationError("参考量列和传感器输出列必须不同。")
    units = {"x": _unit(x_unit), "y": _unit(y_unit)}
    x_index, y_index = headers.index(x_column), headers.index(y_column)
    valid = []
    excluded = []
    for source_line, row in records:
        x, x_error = _number(row[x_index], x_column)
        y, y_error = _number(row[y_index], y_column)
        if x_error or y_error:
            excluded.append({"source_line": source_line, "reason": "；".join(
                error for error in (x_error, y_error) if error
            )})
        else:
            valid.append((source_line, x, y))
    n = len(valid)
    if n < 3:
        raise CalibrationError("至少需要 3 对有效数值；当前有 {} 对，排除了 {} 条记录。".format(n, len(excluded)))
    xs = [row[1] for row in valid]
    ys = [row[2] for row in valid]
    levels = sorted(set(xs))
    if len(levels) < 2:
        raise CalibrationError("至少需要 2 个不同的参考量数值，才能进行线性标定。")

    x_mean, x_normalized, x_scale = _center(xs)
    y_mean, y_normalized, y_scale = _center(ys)
    if x_scale == 0:
        raise CalibrationError("参考量差异小于当前浮点精度，无法进行线性标定。")
    xx = math.fsum(value * value for value in x_normalized)
    if y_scale == 0:
        normalized_slope = 0.0
        slope = 0.0
    else:
        normalized_slope = math.fsum(
            x * y for x, y in zip(x_normalized, y_normalized)
        ) / xx
        slope = _scaled_ratio(normalized_slope, y_scale, x_scale)
    intercept = _finite(y_mean - slope * x_mean)
    normalized_residuals = [
        y - x * normalized_slope for x, y in zip(x_normalized, y_normalized)
    ]
    # Residuals are observed minus fitted in centered coordinates. Computing
    # them before adding the output offset avoids cancellation. Stored fitted
    # outputs are rounded to a float only after that subtraction.
    residuals = [_finite(value * y_scale) for value in normalized_residuals]
    predictions = [_finite(y - residual) for y, residual in zip(ys, residuals)]
    if y_scale == 0:
        r_squared = None
    else:
        ss_total = math.fsum(value * value for value in y_normalized)
        ss_error = math.fsum(value * value for value in normalized_residuals)
        r_squared = _finite(1 - ss_error / ss_total)

    grouped = {}  # type: Dict[float, List[float]]
    for x, y in zip(xs, ys):
        grouped.setdefault(x, []).append(y)
    groups, within_deviations = [], []
    repeat_df, repeated_groups = 0, 0
    for x in levels:
        values = grouped[x]
        count = len(values)
        mean, normalized, scale = _center(values)
        deviations = [value * scale for value in normalized]
        std = None
        if count > 1:
            repeated_groups += 1
            repeat_df += count - 1
            within_deviations.extend(deviations)
            std = _rms(deviations, count - 1)
        groups.append({"x": x, "count": count, "mean_y": mean, "std_y": std})
    warnings = []
    if len(levels) < 3:
        warnings.append("参考量只有 2 个不同水平；建议增加参考量水平，以便检查线性模型是否适合。")
    if y_scale == 0:
        warnings.append("传感器输出为常量，R² 无定义；拟合不能表明传感器对参考量有响应。")
    if repeated_groups == 0:
        warnings.append("没有相同参考量下的重复测量，无法估计重复性；重复性返回空值。")
    if excluded:
        warnings.append("有 {} 条记录因所选列数值无效而被排除，请查看排除记录。".format(len(excluded)))
    return {
        "columns": {"x": x_column, "y": y_column},
        "units": units,
        "counts": {"total": len(records), "valid": n, "excluded": len(excluded)},
        "fit": {"slope": slope, "intercept": intercept, "r_squared": r_squared},
        "metrics": {
            "rmse_y": _rms(residuals, n),
            "residual_std_y": _rms(residuals, n - 2),
            "max_abs_residual_y": max(map(abs, residuals)),
        },
        "repeatability": {
            "pooled_std_y": _rms(within_deviations, repeat_df) if repeat_df else None,
            "degrees_of_freedom": repeat_df,
            "repeated_groups": repeated_groups,
            "groups": groups,
        },
        "points": [
            {"source_line": row[0], "x": row[1], "y": row[2], "predicted_y": predicted, "residual_y": residual}
            for row, predicted, residual in zip(valid, predictions, residuals)
        ],
        "excluded_rows": excluded,
        "warnings": warnings,
    }


def create_bundle(
    text: str, x_column: str, y_column: str, x_unit: str = "", y_unit: str = ""
) -> dict:
    """Capture exact input and settings for reproducible local analysis."""
    analysis = analyze_csv(text, x_column, y_column, x_unit, y_unit)
    return {
        "schema_version": 1,
        "app_version": APP_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "csv_text": text},
        "settings": {
            "x_column": x_column,
            "y_column": y_column,
            "x_unit": analysis["units"]["x"],
            "y_unit": analysis["units"]["y"],
        },
        "analysis": analysis,
    }

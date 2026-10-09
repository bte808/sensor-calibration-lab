# Architecture and analysis contract

This file records the internal API and statistical choices for the initial local implementation.

## Scope

Educational forward sensor calibration: reference x, sensor output y, ordinary least squares y = slope*x + intercept. No metrology certification, uncertainty budget, hardware claims, or data-cleaning product. Original CSV never leaves localhost. Python 3.9+, standard library only.

## Python interface (`calibration/core.py`)

- `APP_VERSION = "1.0.0"`
- `CalibrationError(ValueError)` with human-readable Chinese message.
- `inspect_csv(text: str) -> dict`: UTF-8 text (BOM accepted), auto-detect comma/tab/semicolon. Reject empty/duplicate headers, inconsistent row width, >20,000 data records, >50 columns, empty input/header-only. Ignore blank records. Return `headers: list[str]`, `preview: list[list[str]]` (first 8), `row_count: int`, `delimiter: str`.
- `analyze_csv(text: str, x_column: str, y_column: str, x_unit: str = "", y_unit: str = "") -> dict`: strip numeric whitespace; finite floats only with absolute magnitude <=1e100. Exclude and list records with missing/non-numeric/non-finite/out-of-range selected values. Need >=3 valid pairs and >=2 distinct reference values. Reject same selected column. Unit strings trimmed, <=32 characters, no conversion. Column choices must match headers.
- `create_bundle(text, x_column, y_column, x_unit="", y_unit="") -> dict`: calls analysis, returns `schema_version: 1`, `app_version`, `generated_at` ISO UTC, `source: {sha256, csv_text}`, `settings: {x_column,y_column,x_unit,y_unit}`, `analysis`. SHA256 is of UTF-8 exact input text.

## Analysis JSON

```
{
  "columns": {"x": "reference", "y": "output"},
  "units": {"x": "°C", "y": "V"},
  "counts": {"total": 15, "valid": 15, "excluded": 0},
  "fit": {"slope": 0.02, "intercept": 0.5, "r_squared": 0.99|null},
  "metrics": {"rmse_y": 0.01, "residual_std_y": 0.011, "max_abs_residual_y": 0.02},
  "repeatability": {"pooled_std_y": 0.01|null, "degrees_of_freedom": 10, "repeated_groups": 5, "groups": [{"x":0,"count":3,"mean_y":0.5,"std_y":0.01|null}]},
  "points": [{"source_line":2,"x":0,"y":0.49,"predicted_y":0.5,"residual_y":-0.01}],
  "excluded_rows": [{"source_line":17,"reason":"…"}],
  "warnings": ["…"]
}
```

`source_line` is csv.reader.line_num (physical end line of CSV record). Residual = observed minus fitted. RMSE = sqrt(SSE/n). Residual std = sqrt(SSE/(n-2)). R² = 1-SSE/SST, null for constant output. Group exact numeric reference values, sample std per group (ddof=1), pooled std = sqrt(sum within-group SSE / sum(n_g-1)); singleton groups contribute zero df. Report no repeats as null, never zero. Forward OLS treats x as fixed; repeatability is not uncertainty or accuracy. Warn when fewer than 3 distinct reference levels, constant output, and no repeated groups. Use numerically stable centered/scaled calculations and fail explicitly if results are not finite. No automatic outlier deletion.

## Local HTTP server

- GET `/`, `/app.js`, `/style.css`, `/api/example`, `/api/health`
- POST `/api/inspect` body `{csv_text}` -> inspect result
- POST `/api/analyze` body `{csv_text,x_column,y_column,x_unit,y_unit}` -> bundle
- Errors `{error: message}` with 400; 413 for >2 MiB request.
- Bind only 127.0.0.1; no external assets or upload/storage service.

The UI exports a full JSON bundle (raw CSV and parameters included), row CSV, and printable HTML report. Synthetic example: 6 reference temperature levels, 3 repetitions per level, deterministic output voltage values; filenames and documentation explicitly identify it as synthetic.

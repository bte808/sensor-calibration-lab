# Architecture and analysis contract

This file records the internal API and statistical choices for version 1.0.1.

## Scope

Educational forward sensor calibration: reference x, sensor output y, ordinary least squares y = slope*x + intercept. No metrology certification, uncertainty budget, hardware claims, or data-cleaning product. Original CSV never leaves localhost. Python 3.9+, standard library only.

## Python interface (`calibration/core.py`)

- `APP_VERSION = "1.0.1"`
- `CalibrationError(ValueError)` with human-readable Chinese message.
- `inspect_csv(text: str) -> dict`: UTF-8 text (BOM accepted), auto-detect comma/tab/semicolon. Reject empty/duplicate headers, inconsistent row width, >20,000 data records, >50 columns, empty input/header-only. Ignore blank records. Return `headers: list[str]`, `preview: list[list[str]]` (first 8), `row_count: int`, `delimiter: str`.
- `analyze_csv(text: str, x_column: str, y_column: str, x_unit: str = "", y_unit: str = "") -> dict`: strip numeric whitespace; finite floats only with absolute magnitude <=1e100. Exclude and list records with missing/non-numeric/non-finite/out-of-range selected values, including nonzero decimal text that underflows to zero during float parsing. Need >=3 valid pairs and >=2 distinct reference values. Reject same selected column. Unit strings trimmed, <=32 characters, no conversion. Column choices must match headers.
- `create_bundle(text, x_column, y_column, x_unit="", y_unit="") -> dict`: calls analysis, returns `schema_version: 1`, `app_version`, `generated_at` ISO UTC, `source: {sha256, csv_text}`, `settings: {x_column,y_column,x_unit,y_unit}`, `analysis`. SHA256 is of UTF-8 exact input text.

When fewer than three valid pairs remain, the error includes the valid/excluded counts and up to three excluded records, identifying their physical end line, selected column names, and reasons. It reports the number of additional exclusions without printing them all. Reasons longer than 240 characters are shortened to their first and last 112 characters with a truncation marker. If no records were excluded, the message states exactly how many more valid pairs are needed. These diagnostics change neither the accepted-data calculations nor the report schema, so `APP_VERSION` remains 1.0.1.

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

`source_line` is csv.reader.line_num (physical end line of CSV record). Residual = observed minus fitted. RMSE = sqrt(SSE/n). Residual std = sqrt(SSE/(n-2)). R² = 1-SSE/SST, null for constant output. Group exactly equal parsed reference float values, sample std per group (ddof=1), pooled std = sqrt(sum within-group SSE / sum(n_g-1)); singleton groups contribute zero df. Report no repeats as null, never zero. Forward OLS treats x as fixed; repeatability is not uncertainty or accuracy. Warn when fewer than 3 distinct reference levels, constant output, and no repeated groups. No automatic outlier deletion.

## Numerical representation in 1.0.1

CSV numeric text is first parsed with Python `float`. Each accepted binary float is then converted with `Fraction.from_float`, preserving that parsed value exactly. OLS coefficients, predictions, residuals, SSE, SST, group means, and within-group sums of squares are calculated as exact rational numbers. This avoids intermediate floating-point cancellation for data with large spans or offsets. It does not recover decimal information lost in the original text-to-float conversion; distinct source strings that parse to the same float still belong to the same reference group.

Square roots are evaluated only after an exact rational variance is available, using a local `Decimal` context with precision 80. Decimal is also used to detect nonzero input text that parsed as zero. Final JSON numeric fields are converted to floats independently. Predictions and residuals are not recomputed from already rounded output coefficients, and reported error metrics are not recomputed from already rounded output residuals.

If a nonzero final result overflows, becomes non-finite, or underflows to zero during final float conversion, analysis raises `CalibrationError` with an explicit message. Genuine zero results remain valid. The JSON schema remains version 1; the application version changes because the numerical results may change.

## Local HTTP server

- GET `/`, `/app.js`, `/style.css`, `/api/example`, `/api/health`
- POST `/api/inspect` body `{csv_text}` -> inspect result
- POST `/api/analyze` body `{csv_text,x_column,y_column,x_unit,y_unit}` -> bundle
- Errors `{error: message}` with 400; 413 for >2 MiB request.
- Bind only 127.0.0.1; no external assets or upload/storage service.

The UI exports a full JSON bundle (raw CSV and parameters included), row CSV, and printable HTML report. Synthetic example: 6 reference temperature levels, 3 repetitions per level, deterministic output voltage values; filenames and documentation explicitly identify it as synthetic.

## Browser import state

A real file selection clears the previous accepted CSV, analysis bundle, visible results, preview, and column/unit settings before any size check, file read, UTF-8 decode, or CSV inspection request. Failure retains the attempted file name and an actionable error, marks the source as an import failure, and leaves calculation and export disabled. File read failures and invalid UTF-8 have separate messages. The selected `File` object is captured before clearing the native picker value, allowing the same file name to be selected again after correction.

An empty selection or cancellation does not clear previously accepted data or results and sends no request. Import and analysis operations share a synchronous busy guard: the file picker and example button are disabled until the operation finishes. A programmatically dispatched file change while busy is ignored after clearing the picker; it does not replace the current task's source card. Submitting with no accepted CSV returns without clearing the existing import error.

Loading the synthetic example resets the old dataset before fetching it, with the same failure state as file import. CSV inspection establishes the newly accepted dataset; a later analysis failure retains that dataset, preview, and settings so the user can correct the analysis choices. The import-state and diagnostic changes retain application version 1.0.1 because calculations and report format are unchanged.

## Frontend development checks

Run `node --test tests/test_import_state.js` with Node.js 24. The test uses Node's built-in test runner and VM to exercise the actual `web/app.js` event handlers with a small DOM and local fetch stub. All fixtures are synthetic. It checks import state transitions; it does not replace browser rendering checks or the Python CSV/statistical tests. No npm packages or installation step are required.

The CI workflow configures Node 24 with `actions/setup-node@v7` and runs this command in every OS/Python matrix job. This describes the configured checks, not a result for the current commit. Node is a development/CI tool only; running the application still requires only Python and a browser.

## CLI replay, text encoding, and output commit

`app.py --replay REPORT.json` checks the SHA-256 of the stored source text and recomputes from stored settings. For a report with the current application version, the recomputed analysis must match every stored analysis value. For an older or different version, including 1.0.0 replayed by 1.0.1, recomputation proceeds with a version notice and no exact comparison against the old analysis. An optional presentation/source label is preserved. `--output NEW.json` writes a new report; it never replaces an existing destination.

Analysis comparison follows the types of the recomputed result. Expected float fields accept JSON integers or floats only when conversion yields the identical finite binary64 value; no tolerance is used. For example, a browser can serialize `1.0000000000000001e18` as `1000000000000000100`, which must replay successfully even though Python initially reads the latter as an integer. Booleans are not numbers for this check. Integer counts and source lines remain exact integers; keys, container shapes, list lengths, strings, booleans, and nulls must match strictly. Re-export always uses the recomputed result. This compatibility fix retains application version 1.0.1 because it changes neither the calculations nor the report format, and existing 1.0.1 reports must still receive strict validation.

The CLI retains the existing encodings of stdout and stderr and configures their encoding error handler as `backslashreplace`. Unsupported characters in an ASCII or cp1252 terminal are escaped instead of making a status/error message crash. Source CSV and exported JSON remain UTF-8; terminal display fallback does not alter their content.

Output follows this order:

1. Serialize the entire report using `json.dumps(..., ensure_ascii=False, allow_nan=False)` and encode it as UTF-8 before creating a temporary file.
2. Write a temporary file in the destination directory, flush it, call `os.fsync`, and close it.
3. Commit the completed file without overwriting a destination: Windows uses `os.rename`, which refuses an existing destination on that platform; POSIX uses `os.link`.
4. Clean up the temporary file. A cleanup failure after a successful commit is reported as a warning, not as a failed export.

Serialization, write, or commit failure does not expose a partially written destination. Existing files are preserved. A POSIX filesystem that does not support hard links produces an explicit error; there is no fallback that overwrites a destination or publishes a partial file. This is a runtime atomic-commit guarantee, not a claim of crash or power-loss durability. Abrupt process termination may leave a temporary file behind.

"use strict";

const $ = (id) => document.getElementById(id);
const state = { text: "", name: "", synthetic: false, bundle: null, busy: false, revision: 0 };
const exportIds = ["export-json", "export-csv", "export-html"];
const esc = (value) => String(value).replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const fmt = (value, digits = 6) => value == null ? "未定义" : value === 0 ? "0" : Math.abs(value) >= 1e6 || Math.abs(value) < 1e-3 ? value.toExponential(3) : Number(value.toPrecision(digits)).toString();
const unit = (value) => value || "未标注单位";

function showError(message) {
  $("message").textContent = message;
  $("message").hidden = !message;
}

function setBusy(busy) {
  state.busy = busy;
  $("load-example").disabled = busy;
  $("csv-file").disabled = busy;
  $("settings-fields").disabled = busy || !state.text;
  $("analyze").textContent = busy ? "正在计算…" : "计算标定结果 →";
  exportIds.forEach((id) => { $(id).disabled = busy || !state.bundle; });
}

function invalidate() {
  state.revision++;
  state.bundle = null;
  $("stale-note").hidden = $("results").hidden;
  exportIds.forEach((id) => { $(id).disabled = true; });
}

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "本地服务请求失败。");
  return result;
}

async function importData(name, work) {
  // Clear the accepted dataset before any read, decode, or request can fail.
  invalidate();
  state.text = "";
  state.name = "";
  state.synthetic = false;
  $("results").hidden = true;
  $("stale-note").hidden = true;
  $("empty-state").hidden = false;
  $("preview-section").hidden = true;
  $("preview-table").replaceChildren();
  $("preview-count").textContent = "";
  $("x-column").replaceChildren();
  $("y-column").replaceChildren();
  $("x-unit").value = "";
  $("y-unit").value = "";
  $("source-kind").textContent = "正在检查";
  $("source-name").textContent = name;
  $("source-details").textContent = "读取本地数据…";
  try { await work(); }
  catch (error) {
    $("source-kind").textContent = "导入失败";
    $("source-details").textContent = "没有可计算的数据，请修正文件后重新选择。";
    throw error;
  }
}

async function loadText(text, name, synthetic) {
  const inspected = await api("/api/inspect", { csv_text: text });
  state.text = text;
  state.name = name;
  state.synthetic = synthetic;
  const makeOptions = () => inspected.headers.map((header) => new Option(header, header));
  $("x-column").replaceChildren(...makeOptions());
  $("y-column").replaceChildren(...makeOptions());
  $("y-column").selectedIndex = Math.min(1, inspected.headers.length - 1);
  $("x-unit").value = synthetic ? "°C" : "";
  $("y-unit").value = synthetic ? "V" : "";
  $("source-kind").textContent = synthetic ? "合成示例" : "本地导入";
  $("source-name").textContent = name;
  $("source-details").textContent = `${inspected.row_count} 行 · ${inspected.headers.length} 列 · ${inspected.delimiter === "\t" ? "制表符" : inspected.delimiter === ";" ? "分号" : "逗号"}分隔`;
  $("preview-count").textContent = `前 ${inspected.preview.length} 行 / 共 ${inspected.row_count} 行`;
  $("preview-table").innerHTML = `<thead><tr>${inspected.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${inspected.preview.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  $("preview-section").hidden = false;
}

async function analyze() {
  const settings = { csv_text: state.text, x_column: $("x-column").value, y_column: $("y-column").value, x_unit: $("x-unit").value.trim(), y_unit: $("y-unit").value.trim() };
  const revision = state.revision;
  const bundle = await api("/api/analyze", settings);
  if (revision !== state.revision) return;
  // Presentation metadata supplements, but never replaces, the original CSV and calculation settings.
  bundle.presentation = { source_name: state.name, synthetic_example: state.synthetic };
  state.bundle = bundle;
  render(bundle.analysis);
}

async function operation(work) {
  if (state.busy) return;
  showError("");
  setBusy(true);
  try { await work(); }
  catch (error) { showError(error.message || "无法完成操作，请确认本地服务仍在运行。"); }
  finally { setBusy(false); }
}

$("load-example").addEventListener("click", () => operation(async () => {
  $("csv-file").value = "";
  await importData("合成示例", async () => {
    const example = await api("/api/example");
    await loadText(example.csv_text, example.name, true);
  });
  await analyze();
}));

$("csv-file").addEventListener("change", () => {
  const file = $("csv-file").files[0];
  // Retain the File object, then clear the picker so the same file can be
  // selected again after correction. The source card shows the active name.
  $("csv-file").value = "";
  if (!file || state.busy) return;
  return operation(() => importData(file.name, async () => {
    if (file.size > 1024 * 1024) throw new Error("文件超过 1 MiB。请截取本次标定所需的数据后重试。");
    let bytes;
    try { bytes = await file.arrayBuffer(); }
    catch (_) { throw new Error("无法读取文件。请确认文件仍存在且可读取后重新选择。"); }
    let text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
    catch (_) { throw new Error("文件不是有效的 UTF-8 文本。请从 Excel 另存为 CSV UTF-8 后导入。"); }
    await loadText(text, file.name, false);
  }));
});

$("settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.text) return;
  return operation(async () => { invalidate(); await analyze(); });
});
["x-column", "y-column", "x-unit", "y-unit"].forEach((id) => $(id).addEventListener("input", invalidate));

function chartSVG(analysis, residual = false) {
  const all = analysis.points;
  const limit = 2000;
  const points = all.length <= limit ? all : Array.from({length: limit}, (_, i) => all[Math.round(i * (all.length - 1) / (limit - 1))]);
  const width = 760, height = residual ? 200 : 296;
  const left = 78, right = 25, top = 20, bottom = 48;
  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  for (const point of all) {
    xMin = Math.min(xMin, point.x); xMax = Math.max(xMax, point.x);
    const values = residual ? [point.residual_y, 0] : [point.y, point.predicted_y];
    for (const value of values) { yMin = Math.min(yMin, value); yMax = Math.max(yMax, value); }
  }
  // Subtraction before scaling avoids losing narrow ranges around large offsets.
  const xRange = xMax - xMin;
  let yRange = yMax - yMin;
  if (yRange === 0) { const padding = Math.abs(yMin) * 0.1 || 1; yMin -= padding; yMax += padding; yRange = yMax - yMin; }
  // Offset axes keep distinct ticks readable when the baseline dwarfs the span.
  const xOffset = Math.abs(xMin) > xRange * 100 ? xMin : 0;
  const yOffset = Math.abs(yMin) > yRange * 100 ? yMin : 0;
  const xPixel = (x) => left + (0.035 + 0.93 * ((x - xMin) / xRange)) * (width - left - right);
  const yPixel = (y) => top + (0.92 - 0.84 * ((y - yMin) / yRange)) * (height - top - bottom);
  let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${residual ? "残差分布图" : "传感器线性标定图"}"><title>${residual ? "残差相对参考量的分布，零线为虚线" : "参考量与传感器输出的散点和最小二乘拟合直线"}</title>`;
  for (let i = 0; i <= 4; i++) {
    const y = yMin + yRange * i / 4;
    const py = yPixel(y);
    svg += `<line class="grid" x1="${left}" y1="${py}" x2="${width-right}" y2="${py}"/><text text-anchor="end" x="${left-10}" y="${py+3}">${esc(fmt(yOffset ? yRange * i / 4 : y, 4))}</text>`;
  }
  for (let i = 0; i <= 5; i++) {
    const x = xMin + xRange * i / 5;
    const px = xPixel(x);
    svg += `<text text-anchor="middle" x="${px}" y="${height-bottom+19}">${esc(fmt(xOffset ? xRange * i / 5 : x, 5))}</text>`;
  }
  svg += `<line class="axis" x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}"/>`;
  if (residual) {
    svg += `<line class="zero" x1="${left}" y1="${yPixel(0)}" x2="${width-right}" y2="${yPixel(0)}"/>`;
  } else {
    // Use calculated predictions at observed endpoints to retain the engine's centered precision.
    const low = all.find((p) => p.x === xMin), high = all.find((p) => p.x === xMax);
    svg += `<path class="fit-line" d="M ${xPixel(xMin)} ${yPixel(low.predicted_y)} L ${xPixel(xMax)} ${yPixel(high.predicted_y)}"/>`;
  }
  for (const point of points) {
    const y = residual ? point.residual_y : point.y;
    svg += `<circle class="sample" cx="${xPixel(point.x)}" cy="${yPixel(y)}" r="${points.length > 500 ? 2 : 3.5}"><title>CSV 行 ${point.source_line}: x=${point.x}, ${residual ? "残差" : "y"}=${y}</title></circle>`;
  }
  const xLabel = `${xOffset ? "x − (" + xOffset + ")" : analysis.columns.x}${analysis.units.x ? " / " + analysis.units.x : ""}`;
  const yLabel = `${yOffset ? "y − (" + yOffset + ")" : residual ? "残差" : analysis.columns.y}${analysis.units.y ? " / " + analysis.units.y : ""}`;
  svg += `<text class="axis-label" text-anchor="middle" x="${(width+left-right)/2}" y="${height-7}">${esc(xLabel.length > 70 ? xLabel.slice(0,67)+"…" : xLabel)}</text><text class="axis-label" x="${left}" y="10">${esc(yLabel.length > 70 ? yLabel.slice(0,67)+"…" : yLabel)}</text></svg>`;
  return svg;
}

function render(a) {
  $("results").hidden = false;
  $("empty-state").hidden = true;
  $("stale-note").hidden = true;
  $("synthetic-note").hidden = !state.synthetic;
  $("data-badge").textContent = state.synthetic ? "合成示例 · " + a.counts.valid + " 点" : "本地数据 · " + a.counts.valid + " 点";
  $("slope").textContent = fmt(a.fit.slope);
  $("slope-unit").textContent = `${unit(a.units.y)} / ${unit(a.units.x)}`;
  $("intercept").textContent = fmt(a.fit.intercept);
  $("intercept-unit").textContent = unit(a.units.y);
  $("r-squared").textContent = fmt(a.fit.r_squared);
  $("rmse").textContent = fmt(a.metrics.rmse_y);
  $("rmse-unit").textContent = unit(a.units.y);
  $("equation").textContent = `y = ${fmt(a.fit.slope)} x ${a.fit.intercept < 0 ? "−" : "+"} ${fmt(Math.abs(a.fit.intercept))}`;
  $("fit-chart").innerHTML = chartSVG(a);
  $("residual-chart").innerHTML = chartSVG(a, true);
  $("plot-note").textContent = a.points.length > 2000 ? `曲线显示均匀采样的 2,000 / ${a.points.length} 个点；统计与导出使用全部有效数据。` : "悬停观测点可查看数值与 CSV 行号。";
  $("residual-stat").textContent = `最大 |残差| ${fmt(a.metrics.max_abs_residual_y)} · 残差标准差 ${fmt(a.metrics.residual_std_y)} ${a.units.y}`;
  $("pooled-std").textContent = a.repeatability.pooled_std_y == null ? "无法估计" : `${fmt(a.repeatability.pooled_std_y)} ${a.units.y}`;
  $("repeat-meta").textContent = `${a.repeatability.repeated_groups} 个重复组 · ${a.repeatability.degrees_of_freedom} 自由度`;
  $("groups-body").innerHTML = a.repeatability.groups.map((g) => `<tr><td>${esc(g.x)}</td><td>${g.count}</td><td title="${esc(g.mean_y)}">${esc(fmt(g.mean_y))}</td><td>${g.std_y == null ? "—" : esc(fmt(g.std_y))}</td></tr>`).join("");
  $("counts").innerHTML = [["输入",a.counts.total],["有效",a.counts.valid],["排除",a.counts.excluded]].map(([label,count]) => `<div>${label}<strong>${count}</strong></div>`).join("");
  $("warnings").replaceChildren(...a.warnings.map((warning) => { const li = document.createElement("li"); li.textContent = warning; return li; }));
  $("excluded-details").hidden = a.excluded_rows.length === 0;
  $("excluded-list").replaceChildren(...a.excluded_rows.map((row) => { const li = document.createElement("li"); li.textContent = `CSV 行 ${row.source_line}：${row.reason}`; return li; }));
}

function download(content, mime, extension) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `calibration-${state.synthetic ? "synthetic-" : ""}${new Date().toISOString().replace(/[:.]/g,"-")}.${extension}`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

$("export-json").addEventListener("click", () => {
  if (state.bundle) download(JSON.stringify(state.bundle, null, 2) + "\n", "application/json;charset=utf-8", "json");
});
$("export-csv").addEventListener("click", () => {
  if (!state.bundle) return;
  const header = "source_line,x,y,predicted_y,residual_y";
  const rows = state.bundle.analysis.points.map((p) => [p.source_line,p.x,p.y,p.predicted_y,p.residual_y].join(","));
  download("\uFEFF" + [header,...rows].join("\r\n") + "\r\n", "text/csv;charset=utf-8", "csv");
});

function reportHTML(bundle) {
  const a = bundle.analysis;
  const table = (headers, rows) => `<table><thead><tr>${headers.map((v) => `<th>${esc(v)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>传感器线性标定报告</title><style>body{max-width:900px;margin:40px auto;padding:0 24px;font:14px/1.7 system-ui,sans-serif;color:#203832}h1{font-size:27px}h2{font-size:18px;margin-top:30px}.notice{background:#f5efdf;padding:12px}table{border-collapse:collapse;width:100%;font-size:12px}td,th{border-bottom:1px solid #dde5df;text-align:left;padding:8px}th{background:#f5f7f3}.hash{overflow-wrap:anywhere;font-size:11px}svg{width:100%;height:auto}svg text{font:10px system-ui;fill:#718179}svg .grid{stroke:#edf1eb}svg .axis{stroke:#d6e0d7}svg .fit-line{stroke:#ba8154;stroke-width:1.8;fill:none}svg .sample{fill:#237d68;stroke:white;stroke-width:1}svg .zero{stroke:#99aaa0;stroke-dasharray:4 4}small{color:#718179}@media print{body{margin:0;max-width:none}h2,svg,table{break-inside:avoid}}</style><h1>传感器线性标定报告</h1><p class="notice">${bundle.presentation.synthetic_example ? "本报告使用确定性合成示例，不代表真实实验或传感器性能。" : "本报告使用用户本地导入的数据；工具未核实其来源或实验条件。"} 仅用于学习与展示，不是计量认证工具。</p><p>文件：${esc(bundle.presentation.source_name)}<br>生成时间：${esc(bundle.generated_at)}<br>算法版本：${esc(bundle.app_version)} · 报告格式：${bundle.schema_version}<br>参考列 x：${esc(a.columns.x)}（${esc(unit(a.units.x))}）<br>输出列 y：${esc(a.columns.y)}（${esc(unit(a.units.y))}）</p><p class="hash">源文本 SHA-256：${esc(bundle.source.sha256)}</p><h2>拟合与残差</h2><p>普通最小二乘 y = ax + b；残差为观测输出减去拟合输出。</p>${table(["指标","结果"],[["斜率 a",a.fit.slope],["截距 b",a.fit.intercept],["R²",a.fit.r_squared == null ? "未定义（输出恒定）" : a.fit.r_squared],["RMSE / " + unit(a.units.y),a.metrics.rmse_y],["残差标准差 / " + unit(a.units.y),a.metrics.residual_std_y],["最大绝对残差 / " + unit(a.units.y),a.metrics.max_abs_residual_y],["输入 / 有效 / 排除",`${a.counts.total} / ${a.counts.valid} / ${a.counts.excluded}`]])}${chartSVG(a)}${chartSVG(a,true)}<small>${a.points.length > 2000 ? "图示最多 2,000 个均匀采样点，所有统计使用全部有效数据。" : "图中显示全部有效数据点。"}</small><h2>重复性</h2><p>组内合并标准差：${a.repeatability.pooled_std_y == null ? "无法估计（无重复测量）" : esc(a.repeatability.pooled_std_y) + " " + esc(a.units.y)}；自由度：${a.repeatability.degrees_of_freedom}。</p>${table(["参考量","次数","输出均值","样本标准差"],a.repeatability.groups.map((g) => [g.x,g.count,g.mean_y,g.std_y == null ? "不定义（单次）" : g.std_y]))}<h2>处理记录</h2><ul>${a.warnings.map((w) => `<li>${esc(w)}</li>`).join("") || "<li>无额外计算提示。</li>"}</ul>${a.excluded_rows.length ? table(["CSV 记录结束行","排除原因"],a.excluded_rows.map((r) => [r.source_line,r.reason])) : "<p>没有排除数据行。</p>"}<h2>计算定义与复算</h2><p>RMSE = √(Σe²/n)；残差标准差 = √(Σe²/(n−2))；R² = 1−SSE/SST。参考量按数值完全相等分组，各组样本标准差使用 nⱼ−1 分母，组内合并标准差 = √[ΣⱼΣᵢ(yᵢⱼ−ȳⱼ)² / Σⱼ(nⱼ−1)]。单次组贡献 0 自由度。</p><p>OLS 将参考量视为固定值。高 R² 不代表准确；重复性不等于准确度或不确定度。单位仅作标注；不自动删除离群点。输入先解析为二进制浮点，再以精确有理数计算，开根使用高精度十进制；各结果分别舍入，不能恢复输入解析时丢失的位数。</p><p>请配套保存完整 JSON，内含原始 CSV、设置和分析结果；使用 <code>python3 app.py --replay 报告.json</code> 复算（Windows 可用 <code>py -3</code> 替换 <code>python3</code>）。指纹针对导入后的 UTF-8 源文本，不是文件名或原文件字节编码。</p><small>Sensor Calibration Lab · 无外部服务</small></html>`;
}

$("export-html").addEventListener("click", () => {
  if (state.bundle) download(reportHTML(state.bundle), "text/html;charset=utf-8", "html");
});

setBusy(false);

"use strict";

// Exercise the real app.js event handlers with a deliberately small DOM and
// local fetch stub. These tests cover import state, not browser rendering or
// Python's CSV/statistical calculations. All fixtures are synthetic.
// Run: node --test tests/test_import_state.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { TextDecoder } = require("node:util");

const appSource = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const OLD = "old_reference,old_output\n0,1\n1,3\n2,5\n";
const NEW = "new_reference,new_output\n0,2\n1,4\n2,6\n";
const exportsIds = ["export-json", "export-csv", "export-html"];

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function bytes(text) {
  return Uint8Array.from(Buffer.from(text, "utf8")).buffer;
}

function file(name, text, overrides = {}) {
  return { name, size: Buffer.byteLength(text), arrayBuffer: async () => bytes(text), ...overrides };
}

function response(value, ok = true) {
  return { ok, json: async () => value };
}

class Element {
  constructor(id) {
    this.id = id;
    this.listeners = new Map();
    this.files = [];
    this.children = [];
    this.disabled = false;
    this.hidden = ["results", "preview-section", "stale-note", "message"].includes(id);
    this._value = "";
    this._innerHTML = "";
    this.textContent = "";
    this._selectedIndex = -1;
  }
  get value() { return this._value; }
  set value(value) {
    this._value = value;
    // Browser scripts can clear a file picker; that also empties FileList.
    if (this.id === "csv-file" && value === "") this.files = [];
  }
  get selectedIndex() { return this._selectedIndex; }
  set selectedIndex(index) {
    this._selectedIndex = index;
    this._value = this.children[index] ? this.children[index].value : "";
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = value;
    if (value === "") {
      this.children = [];
      if (this.id.endsWith("-column")) this.selectedIndex = -1;
    }
  }
  replaceChildren(...children) {
    this.children = children;
    this._innerHTML = "";
    if (this.id.endsWith("-column")) this.selectedIndex = children.length ? 0 : -1;
  }
  addEventListener(type, callback) {
    assert.equal(this.listeners.has(type), false, "Only one handler per test event is expected");
    this.listeners.set(type, callback);
  }
  dispatch(type, event = {}) {
    const callback = this.listeners.get(type);
    return callback ? callback(event) : undefined;
  }
  appendChild() {}
  click() {}
  remove() {}
}

function createHarness() {
  const elements = new Map();
  const requests = [];
  const handlers = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  const context = vm.createContext({
    TextDecoder, console, setTimeout,
    Option: function (text, value) { return { text, value }; },
    document: { getElementById: get, createElement: (tag) => new Element(tag), body: { appendChild() {} } },
    fetch: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, payload });
      if (handlers.has(url)) return handlers.get(url)(payload);
      if (url === "/api/inspect") {
        const rows = payload.csv_text.trim().split("\n").map((row) => row.split(","));
        return response({ headers: rows[0], preview: rows.slice(1), row_count: rows.length - 1, delimiter: "," });
      }
      if (url === "/api/analyze") {
        const rows = payload.csv_text.trim().split("\n").slice(1).map((row) => row.split(",").map(Number));
        const points = rows.map(([x, y], index) => ({ source_line: index + 2, x, y, predicted_y: y, residual_y: 0 }));
        return response({ source: { csv_text: payload.csv_text, sha256: "test-only" }, settings: payload, analysis: {
          columns: { x: payload.x_column, y: payload.y_column }, units: { x: payload.x_unit, y: payload.y_unit },
          counts: { total: rows.length, valid: rows.length, excluded: 0 },
          fit: { slope: 2, intercept: rows[0][1], r_squared: 1 },
          metrics: { rmse_y: 0, residual_std_y: 0, max_abs_residual_y: 0 },
          repeatability: { pooled_std_y: null, degrees_of_freedom: 0, repeated_groups: 0, groups: [] },
          points, warnings: [], excluded_rows: [],
        } });
      }
      if (url === "/api/example") return response({ name: "synthetic_temperature.csv", synthetic: true, csv_text: OLD });
      throw new Error("Unexpected fetch: " + url);
    },
  });
  vm.runInContext(appSource, context, { filename: "web/app.js" });
  const state = () => JSON.parse(vm.runInContext("JSON.stringify(state)", context));
  async function until(predicate) {
    for (let attempt = 0; attempt < 40; attempt++) {
      if (predicate()) return;
      await new Promise((resolve) => setImmediate(resolve));
    }
    assert.fail("Event operation did not reach the expected state");
  }
  async function finish(returned) {
    // New handlers can return operation's promise. The bounded idle check also
    // makes the old non-returning handlers observable for the red test run.
    await returned;
    await until(() => !state().busy);
  }
  function beginSelection(selected) {
    const input = get("csv-file");
    input.files = selected ? [selected] : [];
    input._value = selected ? "C:\\fakepath\\" + selected.name : "";
    return input.dispatch("change");
  }
  async function choose(selected) { await finish(beginSelection(selected)); }
  async function submit() {
    await finish(get("settings-form").dispatch("submit", { preventDefault() {} }));
  }
  function summary() {
    return {
      state: state(), sourceName: get("source-name").textContent,
      sourceDetails: get("source-details").textContent, message: get("message").textContent,
      columns: [get("x-column").value, get("y-column").value],
      units: [get("x-unit").value, get("y-unit").value],
      resultsHidden: get("results").hidden, previewHidden: get("preview-section").hidden,
      preview: get("preview-table").innerHTML,
      formDisabled: get("settings-fields").disabled,
      exportsDisabled: exportsIds.map((id) => get(id).disabled),
    };
  }
  return { get, state, requests, handlers, until, finish, beginSelection, choose, submit, summary };
}

async function acceptedOldResult(harness, synthetic = false) {
  if (synthetic) await harness.finish(harness.get("load-example").dispatch("click"));
  else {
    await harness.choose(file("old-valid.csv", OLD));
    harness.get("x-unit").value = "old-x-unit";
    harness.get("y-unit").value = "old-y-unit";
    await harness.submit();
  }
  assert.equal(harness.state().text, OLD);
  assert.ok(harness.state().bundle);
  assert.equal(harness.get("results").hidden, false);
}

function assertNoAcceptedData(harness, selectedName) {
  const current = harness.state();
  assert.equal(current.text, "", "Old CSV must no longer be analyzable");
  assert.equal(current.name, "", "Only accepted data may populate the internal source name");
  assert.equal(current.synthetic, false);
  assert.equal(current.bundle, null);
  assert.equal(harness.get("settings-fields").disabled, true);
  for (const id of exportsIds) assert.equal(harness.get(id).disabled, true, id);
  for (const id of ["x-column", "y-column", "x-unit", "y-unit"]) assert.equal(harness.get(id).value, "", id);
  assert.equal(harness.get("results").hidden, true);
  assert.equal(harness.get("preview-section").hidden, true);
  assert.equal(harness.get("preview-table").innerHTML, "");
  assert.equal(harness.get("csv-file").value, "", "The same file must be selectable again");
  if (selectedName !== undefined) assert.equal(harness.get("source-name").textContent, selectedName);
}

async function assertFailedImport(harness, selected, messagePattern) {
  await harness.choose(selected);
  assertNoAcceptedData(harness, selected.name);
  const message = harness.get("message").textContent;
  assert.match(message, messagePattern);
  const before = harness.requests.length;
  await harness.submit();
  assert.equal(harness.requests.length, before, "Submit without accepted data must not call an API");
  assert.equal(harness.get("message").textContent, message, "Submit must preserve the import error");
}

test("two successful selections replace CSV, columns, units and result provenance", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  await h.choose(file("new-valid.csv", NEW));
  assert.equal(h.state().text, NEW);
  assert.equal(h.state().name, "new-valid.csv");
  assert.equal(h.state().bundle, null);
  assert.equal(h.get("source-name").textContent, "new-valid.csv");
  assert.equal(h.get("csv-file").value, "");
  assert.equal(h.get("settings-fields").disabled, false);
  assert.equal(h.get("x-column").value, "new_reference");
  assert.equal(h.get("y-column").value, "new_output");
  assert.equal(h.get("x-unit").value, "");
  assert.equal(h.get("y-unit").value, "");
  await h.submit();
  const submitted = h.requests.filter((request) => request.url === "/api/analyze").at(-1).payload;
  assert.equal(submitted.csv_text, NEW);
  assert.equal(submitted.x_column, "new_reference");
  assert.equal(h.state().bundle.presentation.source_name, "new-valid.csv");
});

test("oversized replacement removes the previous CSV and cannot resubmit it", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  let reads = 0;
  await assertFailedImport(h, file("oversized.csv", NEW, {
    size: 1024 * 1024 + 1, arrayBuffer: async () => { reads++; return bytes(NEW); },
  }), /1 MiB/);
  assert.equal(reads, 0);
});

test("invalid UTF-8 replacement removes old synthetic data and cannot resubmit it", async () => {
  const h = createHarness();
  await acceptedOldResult(h, true);
  await assertFailedImport(h, file("invalid-utf8.csv", NEW, { arrayBuffer: async () => Uint8Array.from([0xff]).buffer }), /UTF-8/);
});

test("file read failure clears old data and reports a read error, not an encoding diagnosis", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  await assertFailedImport(h, file("unreadable.csv", NEW, { arrayBuffer: async () => { throw new Error("read failed"); } }), /读取|读取失败|read/i);
  assert.doesNotMatch(h.get("message").textContent, /不是有效的 UTF-8/);
});

test("CSV inspection failure keeps the failed filename and leaves no accepted data", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  h.handlers.set("/api/inspect", async () => response({ error: "CSV 表头重复。" }, false));
  await assertFailedImport(h, file("duplicate-header.csv", NEW), /表头重复/);
});

test("a corrected file with the same name can be selected after failure", async () => {
  const h = createHarness();
  await h.choose(file("retry.csv", NEW, { arrayBuffer: async () => Uint8Array.from([0xff]).buffer }));
  assertNoAcceptedData(h, "retry.csv");
  await h.choose(file("retry.csv", NEW));
  assert.equal(h.get("message").textContent, "");
  assert.equal(h.get("settings-fields").disabled, false);
  assert.equal(h.get("csv-file").value, "");
  await h.submit();
  assert.equal(h.state().bundle.source.csv_text, NEW);
  assert.equal(h.state().bundle.presentation.source_name, "retry.csv");
});

test("cancel and empty selections preserve accepted data and make no requests", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  const before = h.summary();
  const count = h.requests.length;
  h.get("csv-file").dispatch("cancel");
  await h.choose(null);
  assert.deepEqual(h.summary(), before);
  assert.equal(h.requests.length, count);
});

test("pending file reading immediately clears old results and disables controls", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  const reading = deferred();
  const operation = h.beginSelection(file("pending-read.csv", NEW, { arrayBuffer: () => reading.promise }));
  try {
    assert.equal(h.state().busy, true);
    assert.equal(h.get("csv-file").disabled, true);
    assertNoAcceptedData(h, "pending-read.csv");
  } finally { reading.resolve(bytes(NEW)); await h.finish(operation); }
  assert.equal(h.state().text, NEW);
});

test("pending API inspection keeps old data unavailable until new data is accepted", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  const checking = deferred();
  h.handlers.set("/api/inspect", () => checking.promise);
  const operation = h.beginSelection(file("pending-check.csv", NEW));
  await h.until(() => h.requests.some((request) => request.url === "/api/inspect" && request.payload.csv_text === NEW));
  try {
    assert.equal(h.state().busy, true);
    assertNoAcceptedData(h, "pending-check.csv");
  } finally {
    checking.resolve(response({ headers: ["new_reference", "new_output"], row_count: 3, preview: [["0", "2"]], delimiter: "," }));
    await h.finish(operation);
  }
  assert.equal(h.state().text, NEW);
});

test("programmatic selection during busy import is ignored and does not replace its provenance", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  const reading = deferred();
  let ignoredReads = 0;
  const operation = h.beginSelection(file("active.csv", NEW, { arrayBuffer: () => reading.promise }));
  try {
    const ignored = h.beginSelection(file("ignored.csv", OLD, { arrayBuffer: async () => { ignoredReads++; return bytes(OLD); } }));
    await ignored;
    assert.equal(h.state().busy, true);
    assert.equal(h.get("csv-file").value, "");
    assert.equal(h.get("source-name").textContent, "active.csv");
    assert.equal(ignoredReads, 0);
  } finally { reading.resolve(bytes(NEW)); await h.finish(operation); }
  assert.equal(h.state().name, "active.csv");
  assert.equal(h.state().text, NEW);
  assert.equal(h.get("source-name").textContent, "active.csv");
});

test("submit without accepted data preserves the import error and makes no request", async () => {
  const h = createHarness();
  await h.choose(file("too-large-first.csv", NEW, { size: 1024 * 1024 + 1 }));
  const before = h.summary();
  const count = h.requests.length;
  await h.submit();
  assert.equal(h.requests.length, count);
  assert.deepEqual(h.summary(), before);
});

test("example fetch starts from cleared state and failure cannot revive old data", async () => {
  const h = createHarness();
  await acceptedOldResult(h);
  const loading = deferred();
  h.handlers.set("/api/example", () => loading.promise);
  const operation = h.get("load-example").dispatch("click");
  try {
    assert.equal(h.state().busy, true);
    assertNoAcceptedData(h);
    assert.notEqual(h.get("source-name").textContent, "old-valid.csv");
  } finally {
    loading.reject(new Error("合成示例请求失败。"));
    await h.finish(operation);
  }
  assertNoAcceptedData(h);
  assert.match(h.get("message").textContent, /合成示例请求失败/);
  const count = h.requests.length;
  await h.submit();
  assert.equal(h.requests.length, count);
  assert.match(h.get("message").textContent, /合成示例请求失败/);
});

test("example analysis failure keeps the newly accepted data available for retry", async () => {
  const h = createHarness();
  await h.choose(file("previous-import.csv", NEW));
  await h.submit();
  h.handlers.set("/api/analyze", async () => response({ error: "示例分析暂时不可用，请重试。" }, false));
  await h.finish(h.get("load-example").dispatch("click"));

  assert.equal(h.state().text, OLD, "The accepted example must replace the previous import");
  assert.equal(h.state().name, "synthetic_temperature.csv");
  assert.equal(h.state().synthetic, true);
  assert.equal(h.state().bundle, null);
  assert.equal(h.get("settings-fields").disabled, false);
  assert.equal(h.get("preview-section").hidden, false);
  assert.equal(h.get("results").hidden, true);
  assert.equal(h.get("x-column").value, "old_reference");
  assert.equal(h.get("y-column").value, "old_output");
  assert.equal(h.get("x-unit").value, "°C");
  assert.equal(h.get("y-unit").value, "V");
  for (const id of exportsIds) assert.equal(h.get(id).disabled, true);
  assert.match(h.get("message").textContent, /示例分析暂时不可用/);
  assert.equal(h.get("source-name").textContent, "synthetic_temperature.csv");
  assert.equal(h.get("source-kind").textContent, "合成示例", "An analysis failure must not be labeled an import failure");

  h.handlers.delete("/api/analyze");
  const inspectionsBeforeRetry = h.requests.filter((request) => request.url === "/api/inspect").length;
  await h.submit();
  assert.equal(h.requests.filter((request) => request.url === "/api/inspect").length, inspectionsBeforeRetry);
  assert.equal(h.state().bundle.source.csv_text, OLD);
  assert.equal(h.state().bundle.presentation.source_name, "synthetic_temperature.csv");
  assert.equal(h.state().bundle.presentation.synthetic_example, true);
  assert.equal(h.get("message").textContent, "");
});

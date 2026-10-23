import { onTestFinished } from "vitest";
import assert from "node:assert/strict";
import { test } from "vitest";
import { readFileSync } from "node:fs";
import { reviewTabs } from "./helpers/review-tabs.mjs";
import { applyCollectManifest } from "../src/platform/browser/catalog/collect-manifest.ts";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import { ReviewPageFieldWriter } from "../src/platform/browser/actions/page-field-writer.ts";
import {
  formControlKind,
  logicalFormControl,
  readFormControlValue,
} from "../src/platform/browser/form-controls.ts";
import {
  createModelScopeReader,
  readModelContext,
  readModelSelection,
} from "../src/platform/browser/collection/model-selection.ts";
import { modelPageScope, validatedModelInput } from "../src/shared/vehicle-model.ts";

const name = "东风商用车 天龙重卡 启航版 420马力 6X4牵引车(DFH4250A4)(国五)";
function fixture(t, role = false, legacy = false) {
  const f = reviewTabs();
  applyCollectManifest(
    JSON.parse(
      readFileSync(
        new URL("./fixtures/collect-manifest-vehicle-source.json", import.meta.url),
        "utf8",
      ),
    ),
  );
  onTestFinished(() => applyCollectManifest(null));
  f.add(f.form, "h3", {}, "基础信息");
  const type = f.add(f.form, "input", { "aria-label": "车辆类型" });
  type.value = "牵引车";
  f.add(f.form, "h3", {}, "规格参数");
  const selects = {};
  for (const [label, value, text] of [
    ["品牌", "6", "东风商用车"],
    ["车系", "23", "东风天龙"],
    ["车型", "34922", name],
  ]) {
    const item = f.add(f.form, "div", { class: "ant-form-item" });
    f.add(item, "label", {}, label);
    const box = f.add(item, "div", {
      class: "ant-select my-select",
      ...(role ? { role: "combobox" } : {}),
    });
    const selected = f.add(
      box,
      "span",
      {
        class: legacy ? "ant-select-selection-selected-value" : "ant-select-selection-item",
        title: text,
      },
      text,
    );
    const input = f.add(box, "input", {
      "aria-label": label,
      ...(role ? { role: "combobox", "aria-controls": `${label}-list` } : {}),
    });
    input.value = value;
    const mirror = role ? f.add(box, "input", { "aria-label": label }) : input;
    mirror.value = value;
    selects[label] = { box, input, mirror, selected };
  }
  return { ...f, selects };
}

for (const role of [false, true]) {
  test(`collector, inventory, query and writer share selected labels rather than 23/34922 (role=${role})`, async (t) => {
    const f = fixture(t, role);
    const snapshot = ReviewPageFieldCollector.collect(f.form);
    assert.equal(snapshot.pageFields["spec.series"], "东风天龙");
    assert.equal(snapshot.pageFields["vehicle.model"], name);
    assert.equal(
      snapshot.reviewFields.find((field) => field.field === "vehicle.model").value,
      name,
    );
    assert.deepEqual(snapshot.ambiguousFields, []);
    assert.equal(
      snapshot.fieldTargets.filter((field) => field.field === "vehicle.model").length,
      1,
    );
    for (const { input, mirror } of Object.values(f.selects)) {
      assert.equal(logicalFormControl(mirror), input);
      assert.equal(formControlKind(input), "select");
      assert.equal(ReviewPageFieldWriter.captureValue(mirror).value, readFormControlValue(input));
    }
    for (const { input } of Object.values(f.selects)) input.isConnected = true;
    const selection = await readModelSelection(snapshot, async (control) => {
      const [label, entry] = Object.entries(f.selects).find(([, entry]) => entry.input === control);
      return {
        selectedValue: { 品牌: "6", 车系: "23", 车型: "34922" }[label],
        selectedLabel: entry.selected.textContent,
        valueField: { 品牌: "id", 车系: "seriesId", 车型: "modelId" }[label],
      };
    });
    const query = validatedModelInput({
      announcementModel: "DFH4250A4",
      engineModel: "DCI450-51",
      pageScope: modelPageScope(selection),
    });
    assert.deepEqual(query.pageScope, {
      brandId: "6",
      brandName: "东风商用车",
      seriesId: "23",
      seriesName: "东风天龙",
      typeLevel: "牵引车",
    });
    f.selects["车型"].input.value = "输入搜索词，不是选中车型";
    assert.equal(ReviewPageFieldCollector.collect(f.form).pageFields["vehicle.model"], name);
    assert.equal(ReviewPageFieldWriter.captureValue(f.selects["车型"].input).value, name);
  });
}

test("legacy selected labels and title keep full model names when the visible text is abbreviated", (t) => {
  const f = fixture(t, true, true);
  f.selects["车型"].selected.textContent = "东风商用车 天龙重卡…";
  assert.equal(readFormControlValue(f.selects["车型"].input), name);
  assert.equal(ReviewPageFieldCollector.collect(f.form).pageFields["vehicle.model"], name);
});

test("an empty innerText does not hide the selected model label stored in textContent", (t) => {
  const f = fixture(t, true);
  for (const { selected } of Object.values(f.selects)) {
    Object.defineProperty(selected, "innerText", { get: () => "" });
    selected.setAttribute("title", "");
  }
  assert.equal(ReviewPageFieldCollector.collect(f.form).pageFields["vehicle.model"], name);
  assert.equal(ReviewPageFieldWriter.captureValue(f.selects["车型"].input).value, name);
  assert.equal(ReviewPageFieldCollector.collect(f.form).pageFields["spec.series"], "东风天龙");
});

test("an empty framework selection never leaks its raw ID or search input as a selected value", (t) => {
  const f = fixture(t);
  const { input, selected, box } = f.selects["车型"];
  selected.textContent = "";
  selected.setAttribute("title", "");
  input.value = "34922";
  f.add(box, "span", { class: "ant-select-selection-placeholder" }, "请选择车型");
  assert.equal(readFormControlValue(input), "");
  assert.equal(ReviewPageFieldWriter.captureValue(input).value, "");
});

test("native select uses its label for comparison while its exact ID remains unchanged", () => {
  const select = {
    tagName: "SELECT",
    value: "34922",
    options: [{ value: "34922", textContent: name }],
  };
  assert.equal(readFormControlValue(select), name);
  assert.equal(select.value, "34922");
});

test("selection capture rejects internal series primary keys and missing selected labels", async (t) => {
  const f = fixture(t, true);
  for (const { input } of Object.values(f.selects)) input.isConnected = true;
  const fields = ReviewPageFieldCollector.collect(f.form);
  const inspect = async (control) => {
    const [label, entry] = Object.entries(f.selects).find(([, entry]) => entry.input === control);
    return {
      selectedValue: "23",
      selectedLabel: entry.selected.textContent,
      valueField: { 品牌: "id", 车系: "id", 车型: "modelId" }[label],
    };
  };
  await assert.rejects(readModelSelection(fields, inspect), /字段语义/);
  await assert.rejects(
    readModelSelection(fields, async () => ({
      selectedValue: "23",
      selectedLabel: "",
      valueField: "id",
    })),
    /身份无法核验/,
  );
});

test("option ID and display label must come from the same collection snapshot", async (t) => {
  const f = fixture(t, true);
  for (const { input } of Object.values(f.selects)) input.isConnected = true;
  const fields = ReviewPageFieldCollector.collect(f.form);
  await assert.rejects(
    readModelSelection(fields, async () => ({
      selectedValue: "9",
      selectedLabel: "读取期间切换后的品牌",
      valueField: "id",
    })),
    /采集期间/,
  );
});

test("a failed model identity preserves verified brand and series for search, but blocks writes", async (t) => {
  const f = fixture(t, true);
  for (const { input } of Object.values(f.selects)) input.isConnected = true;
  const fields = ReviewPageFieldCollector.collect(f.form);
  const inspect = async (control) => {
    const [label, entry] = Object.entries(f.selects).find(([, entry]) => entry.input === control);
    return {
      selectedValue: { 品牌: "6", 车系: "23", 车型: "34922" }[label],
      selectedLabel: label === "车型" ? "" : entry.selected.textContent,
      valueField: { 品牌: "id", 车系: "seriesId", 车型: "modelId" }[label],
    };
  };
  const context = await readModelContext(fields, inspect);
  assert.equal(context.pageSelection, undefined);
  assert.equal(context.pageModelScope.brandId, "6");
  assert.equal(context.pageModelScope.seriesId, "23");
  assert.equal(context.pageModelScope.seriesName, "东风天龙");
  assert.equal(context.pageModelScope.typeLevel, "牵引车");
  assert.match(context.pageSelectionError, /^车型：.*控件缺少选中标签/);
  await assert.rejects(readModelSelection(fields, inspect), /车型：/);
});

test("model context keeps the basic vehicle type distinct from the license's full type", async (t) => {
  const f = fixture(t, true);
  for (const { input } of Object.values(f.selects)) input.isConnected = true;
  const fields = ReviewPageFieldCollector.collect(f.form);
  fields.pageFields["vehicle.type"] = "自卸车";
  fields.pageFields["vehicle.license_type"] = "重型半挂牵引车";
  const inspect = async (control) => {
    const [label, entry] = Object.entries(f.selects).find(([, entry]) => entry.input === control);
    return {
      selectedValue: { 品牌: "6", 车系: "23", 车型: "34922" }[label],
      selectedLabel: entry.selected.textContent,
      valueField: { 品牌: "id", 车系: "seriesId", 车型: "modelId" }[label],
    };
  };
  assert.equal((await readModelContext(fields, inspect)).pageSelection.vehicleType, "自卸车");
  fields.pageFields["vehicle.type"] = "";
  assert.equal((await readModelContext(fields, inspect)).pageSelection.vehicleType, "");
});

test("search reads only verified page scope and permits an empty model without changing parent selectors", async (t) => {
  const f = fixture(t, true);
  for (const { input } of Object.values(f.selects)) input.isConnected = true;
  const inspect = async (control) => {
    const [label, entry] = Object.entries(f.selects).find(([, entry]) => entry.input === control);
    return {
      selectedValue: { 品牌: "6", 车系: "23", 车型: "" }[label],
      selectedLabel: label === "车型" ? "" : entry.selected.textContent,
      valueField: { 品牌: "id", 车系: "seriesId", 车型: "modelId" }[label],
    };
  };
  f.selects["车型"].selected.textContent = "";
  f.selects["车型"].selected.setAttribute("title", "");
  const collect = () => ReviewPageFieldCollector.collect(f.form);
  const snapshot = (await readModelContext(collect(), inspect)).pageModelScope;
  let current = true;
  const read = createModelScopeReader(
    { isCurrent: () => current, snapshot: () => snapshot, collect },
    inspect,
  );
  const result = await read({});
  assert.equal(result.ok, true);
  assert.deepEqual(result.pageScope, {
    brandId: "6",
    brandName: "东风商用车",
    seriesId: "23",
    seriesName: "东风天龙",
    typeLevel: "牵引车",
  });
  f.form.querySelector('[aria-label="车辆类型"]').value = "载货车";
  assert.equal((await read({})).code, "MODEL_PAGE_SCOPE_CHANGED");
  current = false;
  assert.equal((await read({})).code, "PAGE_IDENTITY_CHANGED");
});

test("search scope rejects selection changes during asynchronous inspection", async (t) => {
  const f = fixture(t, true);
  for (const { input } of Object.values(f.selects)) input.isConnected = true;
  let brandReads = 0;
  const inspect = async (control) => {
    const [label, entry] = Object.entries(f.selects).find(([, entry]) => entry.input === control);
    return {
      selectedValue:
        label === "品牌" ? (++brandReads === 1 ? "6" : "7") : label === "车系" ? "23" : "34922",
      selectedLabel: entry.selected.textContent,
      valueField: { 品牌: "id", 车系: "seriesId", 车型: "modelId" }[label],
    };
  };
  const snapshot = {
    brandId: "6",
    brandName: "东风商用车",
    seriesId: "23",
    seriesName: "东风天龙",
    typeLevel: "牵引车",
  };
  const read = createModelScopeReader(
    {
      isCurrent: () => true,
      snapshot: () => snapshot,
      collect: () => ReviewPageFieldCollector.collect(f.form),
    },
    inspect,
  );
  assert.equal((await read({})).code, "MODEL_PAGE_SCOPE_CHANGED");
});

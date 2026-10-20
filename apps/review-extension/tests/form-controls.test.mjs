import { onTestFinished } from "vitest";
import assert from "node:assert/strict";
import { test } from "vitest";
import {readFileSync} from "node:fs";
import {reviewTabs} from "./helpers/review-tabs.mjs";
import {applyCollectManifest} from "../src/platform/browser/catalog/collect-manifest.ts";
import {ReviewPageFieldCollector} from "../src/platform/browser/collection/page-field-collector.ts";
import {ReviewPageFieldWriter} from "../src/platform/browser/actions/page-field-writer.ts";
import {formControlKind, logicalFormControl, readFormControlValue} from "../src/platform/browser/form-controls.ts";
import {modelPageScope, validatedModelInput} from "../src/shared/vehicle-model.ts";

const name = "东风商用车 天龙重卡 启航版 420马力 6X4牵引车(DFH4250A4)(国五)";
function fixture(t, role = false, legacy = false) {
  const f = reviewTabs();
  applyCollectManifest(JSON.parse(readFileSync(new URL("./fixtures/collect-manifest-vehicle-source.json", import.meta.url), "utf8")));
  onTestFinished(() => applyCollectManifest(null));
  f.add(f.form, "h3", {}, "基础信息");
  const type = f.add(f.form, "input", {"aria-label": "车辆类型"}); type.value = "牵引车";
  f.add(f.form, "h3", {}, "规格参数");
  const selects = {};
  for (const [label, value, text] of [["品牌", "6", "东风商用车"], ["车系", "23", "东风天龙"], ["车型", "34922", name]]) {
    const item = f.add(f.form, "div", {class: "ant-form-item"});
    f.add(item, "label", {}, label);
    const box = f.add(item, "div", {class: "ant-select my-select", ...(role ? {role: "combobox"} : {})});
    const selected = f.add(box, "span", {class: legacy ? "ant-select-selection-selected-value" : "ant-select-selection-item", title: text}, text);
    const input = f.add(box, "input", {"aria-label": label, ...(role ? {role: "combobox", "aria-controls": `${label}-list`} : {})});
    input.value = value;
    const mirror = role ? f.add(box, "input", {"aria-label": label}) : input;
    mirror.value = value;
    selects[label] = {box, input, mirror, selected};
  }
  return {...f, selects};
}

for (const role of [false, true]) {
  test(`collector, inventory, query and writer share selected labels rather than 23/34922 (role=${role})`, t => {
    const f = fixture(t, role);
    const snapshot = ReviewPageFieldCollector.collect(f.form);
    assert.equal(snapshot.pageFields["spec.series"], "东风天龙");
    assert.equal(snapshot.pageFields["vehicle.model"], name);
    assert.equal(snapshot.reviewFields.find(field => field.field === "vehicle.model").value, name);
    assert.deepEqual(snapshot.ambiguousFields, []);
    assert.equal(snapshot.fieldTargets.filter(field => field.field === "vehicle.model").length, 1);
    for (const {input, mirror} of Object.values(f.selects)) {
      assert.equal(logicalFormControl(mirror), input);
      assert.equal(formControlKind(input), "select");
      assert.equal(ReviewPageFieldWriter.captureValue(mirror).value, readFormControlValue(input));
    }
    const query = validatedModelInput({announcementModel: "DFH4250A4", engineModel: "DCI450-51", pageScope: modelPageScope(snapshot.pageFields)});
    assert.deepEqual(query.pageScope, {brandName: "东风商用车", seriesName: "东风天龙", typeLevel: "牵引车"});
    f.selects["车型"].input.value = "输入搜索词，不是选中车型";
    assert.equal(ReviewPageFieldCollector.collect(f.form).pageFields["vehicle.model"], name);
    assert.equal(ReviewPageFieldWriter.captureValue(f.selects["车型"].input).value, name);
  });
}

test("legacy selected labels and title keep full model names when the visible text is abbreviated", t => {
  const f = fixture(t, true, true);
  f.selects["车型"].selected.textContent = "东风商用车 天龙重卡…";
  assert.equal(readFormControlValue(f.selects["车型"].input), name);
  assert.equal(ReviewPageFieldCollector.collect(f.form).pageFields["vehicle.model"], name);
});

test("an empty innerText does not hide the selected model label stored in textContent", t => {
  const f = fixture(t, true);
  for (const {selected} of Object.values(f.selects)) {
    Object.defineProperty(selected, "innerText", {get: () => ""});
    selected.setAttribute("title", "");
  }
  assert.equal(ReviewPageFieldCollector.collect(f.form).pageFields["vehicle.model"], name);
  assert.equal(ReviewPageFieldWriter.captureValue(f.selects["车型"].input).value, name);
  assert.equal(ReviewPageFieldCollector.collect(f.form).pageFields["spec.series"], "东风天龙");
});

test("an empty framework selection never leaks its raw ID or search input as a selected value", t => {
  const f = fixture(t);
  const {input, selected, box} = f.selects["车型"];
  selected.textContent = ""; selected.setAttribute("title", ""); input.value = "34922";
  f.add(box, "span", {class: "ant-select-selection-placeholder"}, "请选择车型");
  assert.equal(readFormControlValue(input), "");
  assert.equal(ReviewPageFieldWriter.captureValue(input).value, "");
});

test("native select uses its label for comparison while its exact ID remains unchanged", () => {
  const select = {tagName: "SELECT", value: "34922", options: [{value: "34922", textContent: name}]};
  assert.equal(readFormControlValue(select), name);
  assert.equal(select.value, "34922");
});

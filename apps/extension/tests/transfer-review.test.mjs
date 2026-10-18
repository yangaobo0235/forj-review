import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, test } from "node:test";
import { applyCollectManifest, manifestIgnoresImage } from "../src/platform/browser/catalog/collect-manifest.ts";
import { ReviewBusinessScope } from "../src/platform/browser/collection/business-scope.ts";
import { imageDocumentType } from "../src/platform/browser/collection/material-hints.ts";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import { ReviewPageFieldWriter } from "../src/platform/browser/actions/page-field-writer.ts";
import { readActiveReviewTab, reviewTabCollectionRoot } from "../src/platform/browser/identity/active-review-tab.ts";
import { reviewTabs } from "./helpers/review-tabs.mjs";

const manifest = JSON.parse(readFileSync(new URL("./fixtures/collect-manifest-transfer.json", import.meta.url), "utf8"));
afterEach(() => applyCollectManifest(null));

function fixture(genericNewLabels = false, duplicateIds = false) {
  applyCollectManifest(manifest);
  const f = reviewTabs(["车源审核", "过户审核"], 1);
  f.panels.forEach(panel => { panel.textContent = ""; });
  const row = f.add(f.form, "div", { class: "ant-row" });
  const inputs = new Map();
  const events = [];
  f.document.defaultView.Event = class { constructor(type) { this.type = type; } };
  const sections = duplicateIds ? [...manifest.page_sections].reverse() : manifest.page_sections;
  for (const section of sections) {
    f.add(f.add(row, "div", { class: "ant-col" }), "div", { class: "formItemTitle" }, section.title);
    for (const field of manifest.fields.filter(field => field.reviewable && field.section === section.key)) {
      const item = f.add(f.add(row, "div", { class: "ant-col" }), "div", { class: "ant-form-item" });
      const id = duplicateIds ? field.key.split(".").at(-1) : field.key.replaceAll(".", "_");
      const label = genericNewLabels && section.key === "transfer_new" ? field.aliases.at(-1) : field.label;
      f.add(f.add(item, "div", { class: "ant-form-item-label" }), "label", { for: id }, label);
      const input = f.add(f.add(item, "div", { class: "ant-form-item-control" }), "input", { id, type: "text" });
      input.value = field.key.endsWith(".code") ? "" : field.key + "-原始值";
      input.dispatchEvent = event => events.push({ id, event: event.type });
      input.scrollIntoView = () => events.push({ id, event: "scroll" });
      input.focus = () => events.push({ id, event: "focus" });
      input.animate = () => events.push({ id, event: "highlight" });
      inputs.set(field.key, input);
    }
  }
  return { ...f, inputs, events };
}

for (const generic of [false, true]) {
  test(`过户实际分区采集17个字段，重复名称隔离（新车字段前缀=${!generic}）`, () => {
    const f = fixture(generic);
    const active = readActiveReviewTab(f.document, ["车源审核", "过户审核"]);
    assert.equal(active.label, "过户审核");
    const collected = ReviewPageFieldCollector.collect(reviewTabCollectionRoot(active));
    assert.deepEqual(collected.ambiguousFields, []);
    assert.equal(collected.reviewFields.length, 17);
    assert.deepEqual(collected.reviewFields.map(item => item.field), [...f.inputs.keys()]);
    for (const [field, input] of f.inputs) {
      assert.equal(collected.pageFields[field], input.value, field);
      assert.equal(collected.fieldTargets.find(target => target.field === field)?.element, input, field);
      assert.equal(collected.reviewFields.find(item => item.field === field)?.editable, true, field);
    }
    assert.equal(collected.pageFields["transfer_invoice.code"], undefined);
    assert.equal(collected.pageFields["transfer_new_invoice.code"], undefined);
    assert.equal(collected.pageFields["invoice.invoice_date"], undefined);
  });
}

test("发票控件复用id时使用所属表单项的标签，日期和VIN不漏项且保持DOM顺序", () => {
  const f = fixture(false, true);
  for (const field of ["transfer_invoice.invoice_date", "transfer_new_invoice.invoice_date"]) {
    f.inputs.get(field).value = field.startsWith("transfer_invoice.") ? "2026-09-08" : "2026-09-03";
  }
  const collected = ReviewPageFieldCollector.collect(f.form);
  assert.deepEqual(collected.ambiguousFields, []);
  assert.deepEqual(collected.reviewFields.map(item => item.field), [...f.inputs.keys()]);
  for (const [field, input] of f.inputs) {
    assert.equal(collected.pageFields[field], input.value, field);
    assert.equal(collected.fieldTargets.find(target => target.field === field)?.element, input, field);
  }
  assert.equal(collected.pageFields["invoice.invoice_date"], undefined);
});

test("新车发票类型和两处发票代码不再参与材料读取和回填", () => {
  for (const prefix of ["transfer_invoice", "transfer_new_invoice"]) {
    assert.ok(!manifest.fields.some(field => field.key === `${prefix}.code` || field.key === `${prefix}.code_layout`));
    assert.ok(!manifest.writable_fields.includes(`${prefix}.code`));
  }
  assert.ok(!manifest.fields.some(field => field.key === "transfer_new_invoice.style"));
  assert.ok(!manifest.writable_fields.includes("transfer_new_invoice.style"));
});

test("各材料保留业务归属，登记证3、4页不会满足必传1、2页", () => {
  applyCollectManifest(manifest);
  const groups = ReviewBusinessScope.assign([
    { kind: "label", text: "过户资料" }, { kind: "image", index: 0 }, { kind: "image", index: 1 },
    { kind: "label", text: "新车发票" }, { kind: "image", index: 2 },
  ]);
  assert.deepEqual(groups.map(item => item.businessScope), ["transfer_vehicle", "transfer_vehicle", "transfer_new"]);
  assert.equal(imageDocumentType({ ...groups[0], labelHint: "登记证书1、2页" }), "registration_certificate");
  assert.equal(imageDocumentType({ ...groups[1], labelHint: "二手车发票" }), "used_car_invoice");
  assert.equal(imageDocumentType({ ...groups[2], labelHint: "新车发票" }), "invoice");
  assert.equal(manifestIgnoresImage("登记证书3、4页"), true);
  assert.equal(manifestIgnoresImage("登记证书1、2页"), false);
});

test("缺少分区时不能把两个同名发票号码猜成某一张票", () => {
  applyCollectManifest(manifest);
  const result = ReviewPageFieldCollector.collectCandidates([
    { label: "发票号码", value: "001", section: "unknown", source: "control" },
    { label: "发票号码", value: "002", section: "unknown", source: "control" },
  ]);
  assert.equal(result.pageFields["transfer_invoice.invoice_no"], undefined);
  assert.equal(result.pageFields["transfer_new_invoice.invoice_no"], undefined);
});

test("MyForm 深层组件仍继承本区标题，同名日期、VIN、号码不漏采", () => {
  const f = fixture(true);
  for (const input of f.inputs.values()) {
    const parent = input.parentElement;
    parent.children.splice(parent.children.indexOf(input), 1);
    let wrapper = parent;
    // MyForm + Ant FormItem + 插槽/组件，包装层数不能决定字段业务归属。
    for (let i = 0; i < 12; i++) wrapper = f.add(wrapper, "div", { class: "component-wrapper" });
    wrapper.append(input);
  }
  const collected = ReviewPageFieldCollector.collect(f.form);
  assert.equal(collected.reviewFields.filter(item => item.field).length, 17);
  for (const [field, input] of f.inputs) {
    assert.equal(collected.pageFields[field], input.value, field);
    assert.equal(collected.fieldTargets.find(target => target.field === field)?.element, input, field);
  }
});

test("标题被空白拆分时仍识别同一分区", () => {
  const f = fixture(true);
  for (const heading of f.form.querySelectorAll(".formItemTitle")) {
    heading.textContent = heading.textContent.split("").join(" \n ");
  }
  const collected = ReviewPageFieldCollector.collect(f.form);
  assert.equal(collected.reviewFields.filter(item => item.field).length, 17);
});

test("空白控件保留页面值与写入目标，允许回填且不跨发票", async () => {
  const f = fixture(true);
  const field = "transfer_invoice.invoice_no";
  const input = f.inputs.get(field);
  input.value = "";
  const other = f.inputs.get("transfer_new_invoice.invoice_no").value;
  const collected = ReviewPageFieldCollector.collect(f.form);
  assert.equal(collected.pageFields[field], "");
  assert.equal(collected.reviewFields.find(item => item.field === field)?.editable, true);
  const target = collected.fieldTargets.find(item => item.field === field)?.element;
  assert.equal(target, input);
  const result = await ReviewPageFieldWriter.executeValue(f.document, target,
    { field, value: "00123456789012345678", expectedValue: "" }, undefined,
    { fields: new Set(manifest.writable_fields), controlKinds: new Set(manifest.writable_control_kinds) });
  assert.equal(result.ok, true, result.message);
  assert.equal(input.value, "00123456789012345678");
  assert.equal(f.inputs.get("transfer_new_invoice.invoice_no").value, other);
});

test("文本回填只改对应发票，成功读回后定位高亮，过期值拒绝写入", async () => {
  const f = fixture();
  const target = f.inputs.get("transfer_invoice.buyer_name");
  const old = target.value;
  const other = f.inputs.get("transfer_new_invoice.invoice_no").value;
  const policy = { fields: new Set(manifest.writable_fields), controlKinds: new Set(manifest.writable_control_kinds) };
  const result = await ReviewPageFieldWriter.executeValue(f.document, target,
    { field: "transfer_invoice.buyer_name", value: "合成买方有限公司", expectedValue: old }, undefined, policy);
  assert.equal(result.ok, true, result.message);
  assert.equal(target.value, "合成买方有限公司");
  assert.equal(f.inputs.get("transfer_new_invoice.invoice_no").value, other);
  assert.ok(f.events.some(item => item.event === "highlight"));
  assert.ok(f.events.some(item => item.event === "scroll"));
  assert.equal((await ReviewPageFieldWriter.executeValue(f.document, target,
    { field: "transfer_invoice.buyer_name", value: "不应写入", expectedValue: old }, undefined, policy)).ok, false);
  assert.equal(target.value, "合成买方有限公司");
});

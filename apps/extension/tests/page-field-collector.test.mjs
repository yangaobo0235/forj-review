import assert from "node:assert/strict";
import test from "node:test";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import { applyCollectManifest } from "../src/platform/browser/catalog/collect-manifest.ts";

function loadCollector() {
  return ReviewPageFieldCollector;
}

function element({
  tag = "div",
  text = "",
  directText = text,
  value,
  section = "unknown",
  label = "",
  source = "adjacent",
} = {}) {
  return {
    tagName: tag.toUpperCase(),
    textContent: text,
    innerText: text,
    value,
    dataset: { reviewSection: section },
    getAttribute(name) {
      if (name === "data-review-label") return label || null;
      if (name === "data-review-source") return source || null;
      if (name === "data-direct-text") return directText || null;
      return null;
    },
  };
}

function candidateFixture(candidates) {
  return { candidates };
}

function control({ label = "", value = "", section = "unknown", type = "text" } = {}) {
  return {
    tagName: "INPUT",
    value,
    textContent: "",
    innerText: "",
    dataset: { reviewSection: section },
    getAttribute(name) {
      if (name === "aria-label") return label || null;
      if (name === "type") return type;
      return null;
    },
    closest: () => null,
  };
}

function fixture(...controls) {
  return {
    querySelectorAll(selector) {
      if (selector.includes("contenteditable")) return controls;
      return [];
    },
  };
}

function collect(collector, root) {
  if (root.candidates) {
    return collector.collectCandidates(root.candidates.map((candidate) => ({
      label: candidate.getAttribute("data-review-label"),
      value: candidate.value ?? candidate.textContent,
      section: candidate.dataset.reviewSection,
      source: candidate.getAttribute("data-review-source"),
    })));
  }
  return collector.collect(root);
}

test("identity collection matches live full collection but skips the review inventory", () => {
  const collector = loadCollector();
  const application = control({ label: "申请单编号", value: "CASE-A" });
  const vin = control({ label: "车架号", value: "VIN-A", section: "old_vehicle" });
  const otherVin = control({ label: "车架号", value: "VIN-B", section: "new_vehicle" });
  const date = control({ label: "报废车日期", value: "2026-07-20" });
  const root = fixture(application, vin, otherVin, date);
  const anchors = ["application.id", "old_vehicle.vin"];
  const full = collector.collect(root);
  assert.ok(full.reviewFields.length > 0);
  const sparse = collector.collectIdentity(root, anchors);
  assert.deepEqual(sparse.pageFields, Object.fromEntries(anchors.map(field => [field, full.pageFields[field]])));
  assert.deepEqual(sparse.reviewFields, []);
  assert.deepEqual(sparse.writableTargets, []);
  assert.deepEqual(sparse.fieldTargets.map(item => item.field), anchors);
  vin.value = "VIN-CHANGED";
  assert.equal(collector.collectIdentity(root, anchors).pageFields["old_vehicle.vin"], "VIN-CHANGED");
});

test("identity collection rejects duplicate anchors even when values agree", () => {
  const collector = loadCollector();
  for (const value of ["VIN-A", "VIN-B"]) {
    const root = fixture(control({ label: "新车车架号", value: "VIN-A" }), control({ label: "新车车架号", value }));
    const sparse = collector.collectIdentity(root, ["new_vehicle.vin"]);
    assert.equal(sparse.pageFields["new_vehicle.vin"], undefined);
    assert.deepEqual(sparse.ambiguousFields, collector.collect(root).ambiguousFields);
    assert.deepEqual(sparse.fieldTargets, []);
  }
});

test("identity collection keeps read-only and table anchors", () => {
  const collector = loadCollector();
  const readonly = control({ label: "申请单编号", value: "CASE-A" });
  readonly.readOnly = true;
  const row = element({ tag: "tr", text: "车架号 VIN-A 所有人 张三", section: "old_vehicle" });
  row.children = [element({ tag: "th", text: "车架号" }), element({ tag: "td", text: "VIN-A" }), element({ tag: "th", text: "所有人" }), element({ tag: "td", text: "张三" })];
  const root = { querySelectorAll(selector) {
    if (selector.includes("contenteditable")) return [readonly];
    return selector.startsWith("tr,") ? [row] : [];
  } };
  const fields = ["application.id", "old_vehicle.vin", "old_vehicle.owner"];
  const full = collector.collect(root);
  const sparse = collector.collectIdentity(root, fields);
  assert.deepEqual(sparse.pageFields, { "application.id": "CASE-A", "old_vehicle.vin": "VIN-A", "old_vehicle.owner": "张三" });
  assert.deepEqual(sparse.pageFields, Object.fromEntries(fields.map(field => [field, full.pageFields[field]])));
});

test("label index follows manifest replacement and reset without losing shared-label ambiguity", () => {
  const collector = loadCollector();
  const field = key => ({ key, label: "自定义编号", aliases: ["自定义编号", "自定义编号"], section: "unknown", section_required: false, reviewable: true });
  const manifest = fields => ({ business_type: "test", scopes: [], page_groups: [], materials: [], fields });
  try {
    applyCollectManifest(manifest([field("application.id")]));
    assert.equal(collector.definitionForLabel("自定义编号").field, "application.id");
    applyCollectManifest(manifest([field("vehicle.vin")]));
    assert.equal(collector.definitionForLabel("自定义编号").field, "vehicle.vin");
    assert.deepEqual(collector.collectIdentity(fixture(control({ label: "自定义编号", value: "VIN-A" })), ["vehicle.vin"]).pageFields, { "vehicle.vin": "VIN-A" });
    applyCollectManifest(manifest([field("application.id"), field("vehicle.vin")]));
    assert.equal(collector.definitionForLabel("自定义编号"), null);
  } finally {
    applyCollectManifest(null);
  }
  assert.equal(collector.definitionForLabel("自定义编号"), null);
  assert.equal(collector.definitionForLabel("申请单编号").field, "application.id");
});

test("recognizes the actual scrap date label", () => {
  const collector = loadCollector();

  assert.equal(
    collector.definitionForLabel("报废车日期").field,
    "old_vehicle.recycle_date",
  );
});

test("requires a known section for repeated vehicle fields", () => {
  const collector = loadCollector();

  assert.equal(collector.isSectionRequired("old_vehicle.vin"), true);
  assert.equal(collector.isSectionRequired("new_vehicle.vin"), true);
  assert.equal(collector.isSectionRequired("old_vehicle.owner"), true);
  assert.equal(collector.isSectionRequired("new_vehicle.owner"), true);
  assert.equal(collector.isSectionRequired("old_vehicle.recycle_date"), false);
});

test("collects a labeled form control", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      tag: "input",
      label: "报废车日期",
      value: "2026-07-20",
      source: "control",
    }),
  ]);

  assert.equal(
    collect(collector, root).pageFields["old_vehicle.recycle_date"],
    "2026-07-20",
  );
});

test("collects a form control whose label contains required text", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      tag: "input",
      label: "报废车日期（必填）",
      value: "2026-07-20",
      source: "control",
    }),
  ]);

  assert.equal(
    collect(collector, root).pageFields["old_vehicle.recycle_date"],
    "2026-07-20",
  );
});

test("collects a readonly label and value pair", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "报废车日期",
      text: "2026-07-20",
      source: "structured",
    }),
  ]);

  assert.equal(
    collect(collector, root).pageFields["old_vehicle.recycle_date"],
    "2026-07-20",
  );
});

test("collects adjacent table cells in the old vehicle section", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      tag: "tr",
      label: "报废车辆车架号",
      text: "LJVA39D84DW002198",
      section: "old_vehicle",
      source: "table",
    }),
  ]);

  assert.equal(
    collect(collector, root).pageFields["old_vehicle.vin"],
    "LJVA39D84DW002198",
  );
});

test("collects when label and value are separate sibling nodes", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "报废车日期",
      text: "2026-07-20",
      source: "adjacent",
    }),
  ]);

  assert.equal(
    collect(collector, root).pageFields["old_vehicle.recycle_date"],
    "2026-07-20",
  );
});

test("does not collect a repeated vehicle field without a known section", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({ label: "车架号", text: "NEW-VIN", source: "adjacent" }),
  ]);

  const result = collect(collector, root);

  assert.equal(result.pageFields["old_vehicle.vin"], undefined);
  assert.equal(result.pageFields["new_vehicle.vin"], undefined);
});

test("collects an explicit old vehicle VIN label without a section", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "报废车辆车架号",
      text: "LJVA39D84DW002198",
      source: "structured",
    }),
  ]);

  const result = collect(collector, root);

  assert.equal(result.pageFields["old_vehicle.vin"], "LJVA39D84DW002198");
  assert.equal(result.pageFields["new_vehicle.vin"], undefined);
});

test("collects an explicit old vehicle plate label without a section", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "报废车辆车牌号",
      text: "青A07149",
      source: "structured",
    }),
  ]);

  const result = collect(collector, root);

  assert.equal(result.pageFields["old_vehicle.plate_no"], "青A07149");
  assert.equal(result.pageFields["new_vehicle.plate_no"], undefined);
});

test("does not collect a generic plate label without a known section", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({ label: "车牌号", text: "青A07149", source: "structured" }),
  ]);

  const result = collect(collector, root);

  assert.equal(result.pageFields["old_vehicle.plate_no"], undefined);
  assert.equal(result.pageFields["new_vehicle.plate_no"], undefined);
});

test("does not route a new vehicle VIN into the old vehicle field", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "车架号",
      text: "NEW-VIN",
      section: "new_vehicle",
      source: "table",
    }),
  ]);

  const result = collect(collector, root);

  assert.equal(result.pageFields["old_vehicle.vin"], undefined);
  assert.equal(result.pageFields["new_vehicle.vin"], "NEW-VIN");
});

test("routes an adjacent generic VIN label using its known section", () => {
  const collector = loadCollector();
  const value = element({ text: "NEW-VIN" });
  const label = element({ text: "车架号", section: "new_vehicle" });
  label.nextElementSibling = value;
  const root = {
    querySelectorAll(selector) {
      if (selector === "body *, *") return [label, value];
      return [];
    },
  };

  const result = collector.collect(root);

  assert.equal(result.pageFields["old_vehicle.vin"], undefined);
  assert.equal(result.pageFields["new_vehicle.vin"], "NEW-VIN");
});

test("does not infer a field section from the whole document body", () => {
  const collector = loadCollector();
  const body = element({ tag: "body", text: "新车及发票信息 其他区域 车架号 OLD-VIN" });
  const value = element({ text: "OLD-VIN" });
  const label = element({ text: "车架号" });
  label.nextElementSibling = value;
  label.parentElement = body;
  value.parentElement = body;
  const root = {
    querySelectorAll(selector) {
      if (selector === "body *, *") return [label, value];
      return [];
    },
  };

  const result = collector.collect(root);

  assert.equal(result.pageFields["old_vehicle.vin"], undefined);
  assert.equal(result.pageFields["new_vehicle.vin"], undefined);
});

test("prefers an exact field label over a generic alias", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "车架号",
      text: "OLD-GENERIC-VIN",
      section: "old_vehicle",
      source: "table",
    }),
    element({
      label: "报废车辆车架号",
      text: "OLD-EXACT-VIN",
      section: "old_vehicle",
      source: "structured",
    }),
  ]);

  assert.equal(
    collect(collector, root).pageFields["old_vehicle.vin"],
    "OLD-EXACT-VIN",
  );
});

test("leaves conflicting top-ranked values ambiguous", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "报废车辆车架号",
      text: "VIN-A",
      section: "old_vehicle",
      source: "structured",
    }),
    element({
      label: "报废车辆车架号",
      text: "VIN-B",
      section: "old_vehicle",
      source: "structured",
    }),
  ]);

  const result = collect(collector, root);

  assert.equal(result.pageFields["old_vehicle.vin"], undefined);
  assert.deepEqual(Array.from(result.ambiguousFields), ["old_vehicle.vin"]);
});

test("does not use value length to break equally reliable conflicts", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "报废车辆车架号",
      text: "VIN-A",
      section: "old_vehicle",
      source: "structured",
    }),
    element({
      label: "报废车辆车架号",
      text: "VIN-LONGER",
      section: "old_vehicle",
      source: "structured",
    }),
  ]);

  const result = collect(collector, root);

  assert.equal(result.pageFields["old_vehicle.vin"], undefined);
  assert.deepEqual(Array.from(result.ambiguousFields), ["old_vehicle.vin"]);
});

test("treats specific synonymous labels as equally reliable", () => {
  const collector = loadCollector();
  const root = candidateFixture([
    element({
      label: "报废车辆车架号",
      text: "VIN-A",
      section: "old_vehicle",
      source: "structured",
    }),
    element({
      label: "旧车车架号",
      text: "VIN-B",
      section: "old_vehicle",
      source: "structured",
    }),
  ]);

  const result = collect(collector, root);

  assert.equal(result.pageFields["old_vehicle.vin"], undefined);
  assert.deepEqual(Array.from(result.ambiguousFields), ["old_vehicle.vin"]);
});

test("collects every label-value pair from a multi-pair table row", () => {
  const collector = loadCollector();
  const cells = [
    element({ tag: "th", text: "车架号" }),
    element({ tag: "td", text: "OLD-VIN" }),
    element({ tag: "th", text: "所有人" }),
    element({ tag: "td", text: "张三" }),
  ];
  const row = element({ tag: "tr", text: "车架号 OLD-VIN 所有人 张三", section: "old_vehicle" });
  row.children = cells;
  row.parentElement = null;
  const root = {
    querySelectorAll(selector) {
      if (selector.startsWith("tr,")) return [row];
      return [];
    },
  };

  const result = collector.collect(root);

  assert.equal(result.pageFields["old_vehicle.vin"], "OLD-VIN");
  assert.equal(result.pageFields["old_vehicle.owner"], "张三");
});

test("collects a control wrapped by a native label", () => {
  const collector = loadCollector();
  const wrappingLabel = element({ tag: "label", text: "报废车日期" });
  const control = element({ tag: "input", value: "2026-07-20" });
  control.id = "";
  control.closest = (selector) => selector === "label" ? wrappingLabel : null;
  const root = {
    querySelectorAll(selector) {
      if (selector.includes("input")) return [control];
      return [];
    },
  };

  assert.equal(
    collector.collect(root).pageFields["old_vehicle.recycle_date"],
    "2026-07-20",
  );
});

test("collects an input through an explicit label for association", () => {
  const collector = loadCollector();
  const label = element({ tag: "label", text: "报废车日期" });
  const control = element({ tag: "input", value: "2026-07-20" });
  control.id = "scrap-date";
  control.closest = () => null;
  const root = {
    querySelectorAll(selector) {
      if (selector.includes("input")) return [control];
      if (selector === 'label[for="scrap-date"]') return [label];
      return [];
    },
  };

  assert.equal(
    collector.collect(root).pageFields["old_vehicle.recycle_date"],
    "2026-07-20",
  );
});

test("collects select and plaintext-only contenteditable controls", () => {
  const collector = loadCollector();
  const select = element({ tag: "select", label: "报废车辆类型" });
  select.selectedOptions = [{ textContent: "燃油车" }];
  select.closest = () => null;
  select.getAttribute = (name) => name === "aria-label" ? "报废车辆类型" : null;
  const editable = element({ tag: "div", text: "2026-07-20" });
  editable.closest = () => null;
  editable.getAttribute = (name) => name === "aria-label" ? "报废车日期" : null;
  const root = {
    querySelectorAll(selector) {
      if (selector.includes("contenteditable")) return [select, editable];
      return [];
    },
  };

  const result = collector.collect(root);

  assert.equal(result.pageFields["old_vehicle.type"], "燃油车");
  assert.equal(result.pageFields["old_vehicle.recycle_date"], "2026-07-20");
});

test("ignores a role=combobox affiliation control from a custom form shell", () => {
  const collector = loadCollector();
  const combo = element({ tag: "div", label: "报废车挂靠", section: "old_vehicle" });
  combo.getAttribute = (name) => {
    if (name === "aria-label") return "报废车挂靠";
    if (name === "role") return "combobox";
    return null;
  };
  combo.closest = () => null;
  const root = {
    querySelectorAll(selector) {
      if (selector.includes("contenteditable")) return [combo];
      return [];
    },
  };

  const result = collector.collect(root, "scrap_replacement");

  assert.deepEqual(Array.from(result.writableTargets), []);
  assert.deepEqual(Array.from(result.reviewFields), []);
});

test("collects a readonly two-node container through DOM extraction", () => {
  const collector = loadCollector();
  const label = element({ tag: "span", text: "报废车日期" });
  const value = element({ tag: "span", text: "2026-07-20" });
  const container = element({ text: "报废车日期 2026-07-20" });
  container.children = [label, value];
  container.querySelector = () => null;
  const root = {
    querySelectorAll(selector) {
      if (selector.startsWith("tr,")) return [container];
      return [];
    },
  };

  assert.equal(
    collector.collect(root).pageFields["old_vehicle.recycle_date"],
    "2026-07-20",
  );
});

test("ignores retired transfer fields", () => {
  const result = loadCollector().collectCandidates([
    { label: "车牌号", value: "冀A34870", section: "transfer", source: "control", proximity: 10 },
    { label: "识别车架号", value: "LFWSRX9LXPAC30354", section: "transfer", source: "control", proximity: 10 },
    { label: "过户发票买家名称", value: "石家庄臻誉供应链管理有限公司", section: "transfer", source: "control", proximity: 10 },
    { label: "卖方名称", value: "赞皇县顺红运输有限公司", section: "transfer", source: "control", proximity: 10 },
    { label: "开票日期", value: "2026-08-15", section: "transfer", source: "control", proximity: 10 },
    { label: "车源发布时间", value: "2026-08-13 17:01:31", section: "transfer", source: "structured", proximity: 10 },
  ]);

  assert.deepEqual({ ...result.pageFields }, {});
});

test("ignores transfer controls under a div section title", () => {
  const collector = loadCollector();
  const title = element({ tag: "div", text: "审核过户凭证" });
  const label = element({ tag: "label", text: "开票日期" });
  const formItem = element({ tag: "div", text: "开票日期 2026-08-03" });
  const control = element({ tag: "input", value: "2026-08-03" });
  const card = element({ tag: "div", text: "" });

  card.children = [title, formItem];
  title.parentElement = card;
  formItem.children = [label, control];
  formItem.parentElement = card;
  formItem.querySelector = () => label;
  control.parentElement = formItem;
  control.closest = (selector) => selector.includes(".ant-form-item") ? formItem : null;

  const root = {
    querySelectorAll(selector) {
      if (selector.includes("contenteditable")) return [control];
      return [];
    },
  };

  assert.equal(collector.collect(root).pageFields["transfer.invoice_date"], undefined);
});

test("does not collect transfer date outside supported business sections", () => {
  const collector = loadCollector();
  const control = element({ tag: "input", value: "2026-08-03" });
  control.closest = () => null;
  control.getAttribute = (name) => name === "aria-label" ? "开票日期" : null;
  const root = {
    querySelectorAll(selector) {
      if (selector.includes("contenteditable")) return [control];
      return [];
    },
  };

  assert.equal(collector.collect(root, "transfer").pageFields["transfer.invoice_date"], undefined);
});

test("collects an invoice date label with required marker and colon", () => {
  const result = loadCollector().collectCandidates([
    { label: "* 开票日期：", value: "2026-01-28", section: "new_vehicle", source: "control", proximity: 10 },
  ]);

  assert.equal(result.pageFields["invoice.invoice_date"], "2026-01-28");
});

test("falls back to a date-like invoice candidate when section matching is ambiguous", () => {
  const result = loadCollector().collectCandidates([
    { label: "开票日期", value: "2026-01-28", section: "unknown", source: "control", proximity: 10 },
  ], 1, "scrap_replacement");

  assert.equal(result.pageFields["invoice.invoice_date"], "2026-01-28");
});

test("collects auxiliary page fields without exposing retired affiliation targets", () => {
  const result = loadCollector().collectCandidates([
    { label: "车辆所有人类型", value: "公司", section: "unknown", source: "control" },
    { label: "OCR新车车架号", value: "VIN-NEW", section: "unknown", source: "control" },
    { label: "客户名称", value: "甲运输有限公司", section: "unknown", source: "control" },
    { label: "报废车挂靠", value: "", section: "old_vehicle", source: "control" },
    { label: "新车挂靠", value: "", section: "new_vehicle", source: "control" },
  ]);

  assert.equal(result.pageFields["application.owner_type"], "公司");
  assert.equal(result.pageFields["page_ocr.new_vehicle_vin"], "VIN-NEW");
  assert.equal(result.pageFields["application.customer_name"], "甲运输有限公司");
  assert.deepEqual(Array.from(result.writableTargets), []);
});

test("collects all page-only scrap fields that have no direct material comparison", () => {
  const result = loadCollector().collectCandidates([
    { label: "申请时间", value: "2026-08-27 16:49:24", section: "unknown", source: "control" },
    { label: "报废车辆类型", value: "牵引车", section: "old_vehicle", source: "control" },
    { label: "经销商", value: "武汉昭和商业运营管理有限公司", section: "old_vehicle", source: "control" },
    { label: "新车燃料类型", value: "柴油", section: "new_vehicle", source: "control" },
    { label: "注册日期", value: "2026-06-30", section: "new_vehicle", source: "control" },
    { label: "终端证件号", value: "110101197104249636", section: "new_vehicle", source: "control" },
    { label: "终端客户手机号", value: "17761991004", section: "new_vehicle", source: "control" },
  ]);

  assert.deepEqual({ ...result.pageFields }, {
    "application.submitted_at": "2026-08-27 16:49:24",
    "old_vehicle.type": "牵引车",
    "application.dealer_name": "武汉昭和商业运营管理有限公司",
    "new_vehicle.fuel_type": "柴油",
    "new_vehicle.registration_date": "2026-06-30",
    "application.terminal_certificate_no": "110101197104249636",
    "application.terminal_phone": "17761991004",
  });
});

test("builds the review catalog from editable controls instead of fixed page text", () => {
  const submittedAt = control({ label: "申请时间", value: "2026-08-27 16:49:24" });
  const vehicleType = control({ label: "报废车辆类型", value: "", section: "old_vehicle" });
  const addedField = control({ label: "页面新增字段", value: "新增值", section: "new_vehicle" });
  const affiliation = control({ label: "新车挂靠", value: "", section: "new_vehicle" });
  const readonlyField = control({ label: "只读说明", value: "系统生成" });
  readonlyField.readOnly = true;

  const result = loadCollector().collect(
    fixture(submittedAt, vehicleType, addedField, affiliation, readonlyField),
    "scrap_replacement",
  );

  assert.deepEqual(Array.from(result.reviewFields, (item) => ({ ...item })), [
    {
      field: "old_vehicle.type",
      label: "报废车辆类型",
      value: "",
      controlType: "text",
      editable: true,
      section: "old_vehicle",
      operationOnly: false,
      order: 1,
    },
    {
      field: null,
      label: "页面新增字段",
      value: "新增值",
      controlType: "text",
      editable: true,
      section: "new_vehicle",
      operationOnly: false,
      order: 2,
    },
  ]);
});

test("excludes duplicated affiliation labels and system result controls", () => {
  const oldAffiliation = control({ label: "报废车挂靠 报废车挂靠", value: "", section: "old_vehicle" });
  const newAffiliation = control({ label: "新车挂靠 新车挂靠", value: "", section: "new_vehicle" });
  const reviewResult = control({ label: "审核结果 通过", value: "true" });

  const result = loadCollector().collect(
    fixture(oldAffiliation, newAffiliation, reviewResult),
    "scrap_replacement",
  );

  assert.deepEqual(Array.from(result.reviewFields), []);
});

test("scopes collection to the open approval modal when list filters reuse ids", () => {
  const collector = loadCollector();
  const filterDealer = control({ label: "经销商", value: "" });
  const filterCertificate = control({ label: "报废证明编号", value: "" });
  const approvalType = control({ label: "报废车辆类型", value: "牵引车", section: "old_vehicle" });
  const approvalDate = control({ label: "报废交车日期", value: "2026-02-05", section: "old_vehicle" });
  const modal = {
    querySelectorAll(selector) {
      if (selector.includes("contenteditable")) return [approvalType, approvalDate];
      return [];
    },
  };
  const root = {
    querySelector(selector) {
      return selector.includes(".my-page-modal") ? modal : null;
    },
    querySelectorAll(selector) {
      if (selector.includes("contenteditable")) return [filterDealer, filterCertificate, approvalType, approvalDate];
      return [];
    },
  };

  const result = collector.collect(root, "scrap_replacement");
  assert.equal(result.reviewFields[0].label, "报废车辆类型");
  assert.equal(result.reviewFields[1].label, "报废交车日期");
  assert.equal(result.reviewFields.some((item) => item.label === "经销商"), false);
});

test("configured occurrence counts certificate controls inside the approval detail only", () => {
  const filter = control({ label: "报废证明编号", value: "", section: "old_vehicle" });
  const hidden = control({ label: "报废证明编号", value: "HIDDEN", section: "old_vehicle" });
  hidden.hidden = true;
  const target = control({ label: "报废证明编号", value: "HS-SYNTHETIC-001", section: "old_vehicle" });
  const modal = fixture(hidden, target);
  const root = fixture(filter, hidden, target);
  root.querySelector = () => modal;
  for (const input of [filter, hidden, target]) input.ownerDocument = {
    querySelectorAll: () => [filter, hidden, target],
  };
  applyCollectManifest({ business_type: "scrap_replacement", scopes: ["old_vehicle"], materials: [], page_groups: [], fields: [{
    key: "scrap_certificate.certificate_no", label: "报废证明编号", aliases: ["报废证明编号"],
    section: "old_vehicle", occurrence: 1, reviewable: true,
  }] });
  try {
    const result = loadCollector().collect(root, "scrap_replacement");
    assert.equal(result.pageFields["scrap_certificate.certificate_no"], "HS-SYNTHETIC-001");
    assert.equal(result.reviewFields.length, 1);
    assert.equal(result.reviewFields[0].field, "scrap_certificate.certificate_no");
    assert.equal(result.fieldTargets[0].element, target);
  } finally {
    applyCollectManifest(null);
  }
});

test("explicit occurrence selects the second visible control in the current detail", () => {
  const inputs = [1, 2].map(index => control({ label: "自定义编号", value: `CERT-${index}`, section: "old_vehicle" }));
  applyCollectManifest({ business_type: "scrap_replacement", scopes: ["old_vehicle"], materials: [], page_groups: [], fields: [{
    key: "custom.certificate_no", aliases: ["自定义编号"], section: "old_vehicle", occurrence: 2, reviewable: true,
  }] });
  try {
    const result = loadCollector().collect(fixture(...inputs), "scrap_replacement");
    assert.equal(result.pageFields["custom.certificate_no"], "CERT-2");
    assert.equal(result.reviewFields[0].field, null);
    assert.equal(result.reviewFields[1].field, "custom.certificate_no");
    assert.equal(result.fieldTargets[0].element, inputs[1]);
  } finally {
    applyCollectManifest(null);
  }
});

test("keeps read-only known fields in the review catalog without making them writable", () => {
  const collector = loadCollector();
  const date = control({ label: "报废交车日期", value: "2026-02-05", section: "old_vehicle" });
  date.readOnly = true;
  const root = fixture(date);
  const result = collector.collect(root, "scrap_replacement");
  assert.equal(result.reviewFields[0].label, "报废交车日期");
  assert.equal(result.reviewFields[0].editable, false);
});

test("does not expose duplicate retired affiliation controls", () => {
  const result = loadCollector().collectCandidates([
    { label: "报废车挂靠", value: "", section: "old_vehicle", source: "control" },
    { label: "新车挂靠", value: "", section: "new_vehicle", source: "control" },
    { label: "新车挂靠", value: "", section: "new_vehicle", source: "control" },
  ]);

  assert.deepEqual(Array.from(result.writableTargets), []);
  assert.equal(Array.from(result.ambiguousFields).includes("new_vehicle.affiliation"), false);
});

test("treats a standalone invoice currency symbol as an empty amount", () => {
  const result = loadCollector().collectCandidates([
    { label: "开票金额", value: "￥", section: "new_vehicle", source: "control" },
  ]);

  assert.equal(result.pageFields["invoice.amount"], "");
});

test("only one accepted DOM candidate becomes a review target", () => {
  const collector = loadCollector();
  const target = control({ label: "新车车架号", value: "VIN-1" });
  const result = collector.collect(fixture(target), "scrap_replacement");
  assert.equal(result.fieldTargets[0].field, "new_vehicle.vin");
  assert.equal(result.fieldTargets[0].element, target);
});

test("keeps an empty known control as a writeback target", () => {
  const collector = loadCollector();
  const target = control({ label: "报废发动机型号", value: "", section: "old_vehicle" });
  const result = collector.collect(fixture(target), "scrap_replacement");

  assert.equal(result.pageFields["old_vehicle.engine_model"], "");
  assert.equal(result.fieldTargets[0].field, "old_vehicle.engine_model");
  assert.equal(result.fieldTargets[0].element, target);
});

test("ignores invoice verification buttons when collecting invoice number", () => {
  const invoiceNumber = control({ label: "发票号码", value: "26232000000731322946", section: "new_vehicle" });
  const verifyButton = control({ label: "发票号码 一键验真", value: "一键验真", section: "new_vehicle", type: "button" });

  const result = loadCollector().collect(fixture(invoiceNumber, verifyButton), "scrap_replacement");

  assert.equal(result.pageFields["invoice.invoice_no"], "26232000000731322946");
  assert.equal(result.reviewFields.some((item) => item.value === "一键验真"), false);
});

test("equally ranked duplicate DOM candidates do not expose a target", () => {
  const collector = loadCollector();
  const result = collector.collect(fixture(
    control({ label: "新车车架号", value: "VIN-1" }),
    control({ label: "新车车架号", value: "VIN-1" }),
  ), "scrap_replacement");
  assert.equal(result.fieldTargets.some(item => item.field === "new_vehicle.vin"), false);
  assert.ok(result.ambiguousFields.includes("new_vehicle.vin"));
});

test("equally ranked duplicate DOM candidates do not expose a page field value", () => {
  const collector = loadCollector();
  const result = collector.collect(fixture(
    control({ label: "新车车架号", value: "VIN-1" }),
    control({ label: "新车车架号", value: "VIN-1" }),
  ), "scrap_replacement");
  assert.equal(result.pageFields["new_vehicle.vin"], undefined);
  assert.ok(result.unmatchedLabels.includes("新车车架号"));
});

test("conflicting DOM candidates do not expose a target", () => {
  const collector = loadCollector();
  const result = collector.collect(fixture(
    control({ label: "新车车架号", value: "VIN-1" }),
    control({ label: "新车车架号", value: "VIN-2" }),
  ), "scrap_replacement");
  assert.equal(result.fieldTargets.some(item => item.field === "new_vehicle.vin"), false);
  assert.ok(result.ambiguousFields.includes("new_vehicle.vin"));
  assert.equal(result.pageFields["new_vehicle.vin"], undefined);
});

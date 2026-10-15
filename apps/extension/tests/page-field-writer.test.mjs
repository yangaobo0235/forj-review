import assert from "node:assert/strict";
import test from "node:test";
import { ReviewPageFieldWriter } from "../src/platform/browser/actions/page-field-writer.ts";

function loadWriter(globalOverrides = {}) {
  Object.assign(globalThis, globalOverrides);
  return ReviewPageFieldWriter;
}

function invoiceTarget(buttonCount = 1) {
  let clicked = 0;
  const buttons = Array.from({ length: buttonCount }, () => ({ textContent: "一键验真", isConnected: true,
    getAttribute: () => null, click: () => clicked++ }));
  const item = { querySelectorAll: () => buttons };
  const target = { tagName: "INPUT", value: "INV-001", isConnected: true,
    getAttribute: () => null, closest: () => item };
  const root = { querySelectorAll: () => [], contains: node => node === item };
  return { root, target, clicked: () => clicked };
}

test("invoice verification binds to the collected control and its unchanged value", () => {
  const fixture = invoiceTarget();
  assert.equal(loadWriter().triggerInvoiceVerification(fixture.root, fixture.target, "INV-001").ok, true);
  assert.equal(fixture.clicked(), 1);
  fixture.target.value = "INV-002";
  assert.equal(loadWriter().triggerInvoiceVerification(fixture.root, fixture.target, "INV-001").ok, false);
  assert.equal(fixture.clicked(), 1);
});

test("missing or repeated invoice buttons do not trigger a global fallback", () => {
  for (const count of [0, 2]) {
    const fixture = invoiceTarget(count);
    assert.equal(loadWriter().triggerInvoiceVerification(fixture.root, fixture.target, "INV-001").ok, false);
    assert.equal(fixture.clicked(), 0);
  }
});

function valueInput(initial = "", tagName = "INPUT", type = null) {
  return {
    tagName,
    value: initial,
    isConnected: true,
    getAttribute: (name) => (name === "type" ? type : null),
    dispatchEvent() {},
  };
}

test("manual values are read back before centering, focusing and highlighting the target", async () => {
  const input = valueInput("HS-OLD");
  const calls = [];
  input.scrollIntoView = (options) => { assert.equal(input.value, "HS-MANUAL"); calls.push(options.block); };
  input.focus = () => calls.push("focus");
  input.animate = () => calls.push("highlight");
  const result = await loadWriter({ setTimeout }).executeValue(
    { defaultView: { Event: class Event {} } }, input,
    { field: "scrap_certificate.certificate_no", value: "HS-MANUAL", expectedValue: "HS-OLD" },
  );
  assert.equal(result.ok, true);
  assert.deepEqual(calls, ["center", "focus", "highlight"]);
  assert.equal(result.actions[0].value, "HS-MANUAL");
});

test("manual input supports phone fields and does not focus a failed or readonly write", async () => {
  const input = valueInput("13800000000");
  let focused = false;
  input.focus = () => { focused = true; };
  const writer = loadWriter({ setTimeout });
  const root = { defaultView: { Event: class Event {} } };
  input.readOnly = true;
  assert.equal((await writer.executeValue(root, input, { field: "application.terminal_phone", value: "13900000000" })).ok, false);
  assert.equal(focused, false);
  input.readOnly = false;
  assert.equal((await writer.executeValue(root, input, { field: "application.terminal_phone", value: "13900000000" })).ok, true);
  assert.equal(input.value, "13900000000");
  assert.equal(focused, true);
});

test("single-field writer enforces the scrap field allowlist and collected value", async () => {
  const writer = loadWriter({ setTimeout });
  const input = valueInput("VIN-OLD");
  const root = { defaultView: { Event: class Event {} } };

  const denied = await writer.executeValue(root, input, { field: "application.customer_name", value: "张三", expectedValue: "" });
  assert.equal(denied.ok, false);
  assert.equal(input.value, "VIN-OLD");

  const stale = await writer.executeValue(root, input, { field: "new_vehicle.vin", value: "VIN-NEW", expectedValue: "VIN-OTHER" });
  assert.equal(stale.ok, false);
  assert.match(stale.message, /当前值与采集时不同/);
  assert.equal(input.value, "VIN-OLD");
});

test("写回策略由后端下发的白名单决定：字段与控件类型两道闸", async () => {
  const writer = loadWriter({ setTimeout });
  const root = { defaultView: { Event: class Event {} } };
  const text = valueInput("旧值");
  const policy = {
    fields: new Set(["new_vehicle.vin"]),
    controlKinds: new Set(["text", "textarea", "number"]),
  };

  // 清单之外的字段一律拒绝，即使控件本身可写。
  const denied = await writer.executeValue(
    root, text, { field: "application.customer_name", value: "张三" }, undefined, policy,
  );
  assert.equal(denied.ok, false);
  assert.match(denied.message, /不在允许回填范围内/);
  assert.equal(text.value, "旧值");

  // 清单内的文本框正常写入。
  const written = await writer.executeValue(
    root, text, { field: "new_vehicle.vin", value: "VIN-NEW", expectedValue: "旧值" }, undefined, policy,
  );
  assert.equal(written.ok, true);

  // 下拉控件即使字段在清单里也不写：下拉和日期的写回还没有验证过。
  const rejected = await writer.executeValue(
    root, valueInput("", "SELECT"), { field: "new_vehicle.vin", value: "VIN-NEW" }, undefined, policy,
  );
  assert.equal(rejected.ok, false);
  assert.match(rejected.message, /控件类型暂不支持/);

  // 业务声明"不限制"（空集合）时按字段清单放行——历史口径不变。
  const unrestrictedPolicy = { fields: policy.fields, controlKinds: new Set() };
  const allowedField = await writer.executeValue(
    root, valueInput("旧值"), { field: "new_vehicle.vin", value: "VIN-NEW", expectedValue: "旧值" },
    undefined, unrestrictedPolicy,
  );
  assert.equal(allowedField.ok, true);

  assert.equal(writer.controlKind(valueInput("x")), "text");
  assert.equal(writer.controlKind(valueInput("", "SELECT")), "select");
  assert.equal(writer.controlKind(valueInput("", "INPUT", "date")), "date");
});

test("组合字段回填用同一份业务白名单，不退回内置的报废置换表", async () => {
  // 组合字段以前只认内置的 `ALLOWED_VALUE_FIELDS`（报废置换那张表），别的业务
  // 一来就被判成"不在报废置换允许回填范围内"——过户的发票代码/号码正是如此。
  // 报错信息里还写着报废置换，排查的人会以为是业务选错了。
  const writer = loadWriter({ setTimeout });
  const root = { defaultView: { Event: class Event {} } };
  const policy = {
    fields: new Set(["transfer.invoice_code", "transfer.invoice_no"]),
    controlKinds: new Set(["text", "textarea", "number"]),
  };
  const entries = [
    { field: "transfer.invoice_code", element: valueInput("旧代码"), expectedValue: null },
    { field: "transfer.invoice_no", element: valueInput("旧号码"), expectedValue: null },
  ];
  const action = {
    fields: ["transfer.invoice_code", "transfer.invoice_no"],
    value: "26372000003211765066",
    expectedValues: { "transfer.invoice_code": null, "transfer.invoice_no": null },
  };

  // 没有业务清单时退回内置表，过户的字段本来就不在里面——这正是修复前的表现。
  const denied = await writer.executeValueGroup(root, entries, action, undefined);
  assert.equal(denied.ok, false);
  assert.match(denied.message, /不在本业务允许回填范围内/);

  const allowed = await writer.executeValueGroup(root, entries, action, undefined, policy);
  assert.equal(allowed.ok, true);

  // 控件类型闸同样适用于组合字段。
  const selectEntries = [
    { field: "transfer.invoice_code", element: valueInput("", "SELECT"), expectedValue: null },
    { field: "transfer.invoice_no", element: valueInput("旧号码"), expectedValue: null },
  ];
  const rejected = await writer.executeValueGroup(root, selectEntries, action, undefined, policy);
  assert.equal(rejected.ok, false);
  assert.match(rejected.message, /控件类型暂不支持/);
});

test("single-field writer restores the original value after failed readback", async () => {
  const input = valueInput("VIN-OLD");
  let events = 0;
  input.dispatchEvent = () => {
    events += 1;
    if (events <= 2) input.value = "FRAMEWORK-REJECTED";
  };
  const result = await loadWriter({ setTimeout }).executeValue(
    { defaultView: { Event: class Event {} } },
    input,
    { field: "new_vehicle.vin", value: "VIN-NEW", expectedValue: "VIN-OLD" },
  );

  assert.equal(result.ok, false);
  assert.match(result.message, /回读失败/);
  assert.equal(input.value, "VIN-OLD");
});

test("invoice amount accepts the page currency formatting during readback", async () => {
  const input = valueInput("￥");
  input.dispatchEvent = (event) => {
    if (event.type === "change") input.value = "￥423,000.00";
  };

  const result = await loadWriter({ setTimeout }).executeValue(
    { defaultView: { Event: class Event { constructor(type) { this.type = type; } } } },
    input,
    { field: "invoice.amount", value: "423000.00", expectedValue: "" },
  );

  assert.equal(result.ok, true);
  assert.equal(input.value, "￥423,000.00");
  assert.equal(result.actions[0].value, "￥423,000.00");
});

test("custom field writes only use their owned listbox and reject duplicate options", async () => {
  for (const ownedCount of [0, 1, 2]) {
    let foreignClicks = 0;
    const input = valueInput("OLD");
    input.getAttribute = name => name === "role" ? "combobox" : name === "aria-controls" ? "owned-options" : null;
    input.click = () => {};
    const option = () => ({ textContent: "NEW", isConnected: true, getAttribute: () => null,
      click: () => { input.value = "NEW"; } });
    const root = { defaultView: { Event: class Event {} },
      getElementById: id => id === "owned-options" ? { querySelectorAll: () => Array.from({ length: ownedCount }, option) } : null,
      querySelectorAll: () => [{ querySelectorAll: () => [{ ...option(), click: () => foreignClicks++ }] }],
    };
    const result = await ReviewPageFieldWriter.executeValue(root, input,
      { field: "new_vehicle.fuel_type", value: "NEW", expectedValue: "OLD" }, () => true);
    assert.equal(result.ok, ownedCount === 1);
    assert.equal(input.value, ownedCount === 1 ? "NEW" : "OLD");
    assert.equal(foreignClicks, 0);
  }
});

test("a rejected original-value restore is reported as a rollback failure", async () => {
  const input = valueInput("OLD");
  input.dispatchEvent = () => { input.value = "FRAMEWORK-REJECTED"; };
  const result = await ReviewPageFieldWriter.executeValue(
    { defaultView: { Event: class Event {} } }, input,
    { field: "new_vehicle.vin", value: "NEW", expectedValue: "OLD" }, () => true);
  assert.equal(result.ok, false);
  assert.match(result.message, /原值恢复失败/);
});

test("composite rollback reads both restored values before reporting success", async () => {
  const first = valueInput("FIRST");
  const second = valueInput("SECOND");
  second.dispatchEvent = () => { second.value = "FRAMEWORK-REJECTED"; };
  const result = await ReviewPageFieldWriter.executeValueGroup(
    { defaultView: { Event: class Event {} } },
    [{ field: "invoice.code", element: first }, { field: "invoice.invoice_no", element: second }],
    { fields: ["invoice.code", "invoice.invoice_no"], value: "NEW", expectedValues: { "invoice.code": "FIRST", "invoice.invoice_no": "SECOND" } },
    () => true);
  assert.equal(result.ok, false);
  assert.equal(first.value, "FIRST");
  assert.match(result.message, /自动回滚未完成/);
});

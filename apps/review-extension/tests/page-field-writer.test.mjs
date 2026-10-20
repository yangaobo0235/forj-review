import assert from "node:assert/strict";
import { test } from "vitest";
import { ReviewPageFieldWriter } from "../src/platform/browser/actions/page-field-writer.ts";
import { calendarDate } from "../src/platform/browser/actions/date-picker.ts";

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
    getAttribute: () => null, closest: selector => selector.includes(".ant-form-item") ? item : null };
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

test("generic dependent writes use the caller's fields and final focus target", async () => {
  const controls = {"permit.number": valueInput("OLD"), "permit.date": valueInput("2026-01-01")};
  const focused = [], highlighted = [];
  for (const [field, input] of Object.entries(controls)) {
    input.focus = () => focused.push(field);
    input.animate = () => highlighted.push(field);
  }
  const values = [
    {field: "permit.number", label: "许可证号", value: "NEW"},
    {field: "permit.date", label: "发证日期", value: "2026-10-17"},
  ];
  const result = await ReviewPageFieldWriter.executeSequence(
    {defaultView: {Event: class Event {}}}, values, field => controls[field], () => true,
    {fields: new Set(Object.keys(controls)), label: "许可证信息", focusField: "permit.date",
      settleAfter: {"permit.number": 1}},
  );
  assert.equal(result.ok, true);
  assert.equal(result.message, "许可证信息已整块回填并逐项回读");
  assert.equal(controls["permit.number"].value, "NEW");
  assert.equal(controls["permit.date"].value, "2026-10-17");
  assert.equal(focused.at(-1), "permit.date");
  assert.deepEqual(highlighted.slice(-2), ["permit.number", "permit.date"]);
});

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

test("composite fill reveals the first target once after every value is accepted and highlights all targets", async () => {
  const first = valueInput("FIRST");
  const second = valueInput("SECOND");
  const calls = [];
  for (const [index, input] of [first, second].entries()) {
    input.scrollIntoView = options => {
      assert.equal(first.value, "NEW");
      assert.equal(second.value, "NEW");
      assert.deepEqual(options, { behavior: "smooth", block: "center", inline: "nearest" });
      calls.push(`scroll:${index}`);
    };
    input.focus = options => {
      assert.deepEqual(options, { preventScroll: true });
      calls.push(`focus:${index}`);
    };
    input.animate = (frames, options) => {
      assert.equal(options.duration, 4000);
      assert.equal(frames[1].offset, 0.85);
      calls.push(`highlight:${index}`);
    };
  }
  const result = await ReviewPageFieldWriter.executeValueGroup(
    { defaultView: { Event: class Event {} } },
    [{ field: "invoice.code", element: first }, { field: "invoice.invoice_no", element: second }],
    { fields: ["invoice.code", "invoice.invoice_no"], value: "NEW", expectedValues: { "invoice.code": "FIRST", "invoice.invoice_no": "SECOND" } },
    () => true,
  );
  assert.equal(result.ok, true);
  assert.equal(result.actions.length, 2);
  assert.deepEqual(calls, ["scroll:0", "focus:0", "highlight:0", "highlight:1"]);
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
  const revealCalls = [];
  for (const input of [first, second]) {
    input.scrollIntoView = () => revealCalls.push("scroll");
    input.focus = () => revealCalls.push("focus");
    input.animate = () => revealCalls.push("highlight");
  }
  second.dispatchEvent = () => { second.value = "FRAMEWORK-REJECTED"; };
  const result = await ReviewPageFieldWriter.executeValueGroup(
    { defaultView: { Event: class Event {} } },
    [{ field: "invoice.code", element: first }, { field: "invoice.invoice_no", element: second }],
    { fields: ["invoice.code", "invoice.invoice_no"], value: "NEW", expectedValues: { "invoice.code": "FIRST", "invoice.invoice_no": "SECOND" } },
    () => true);
  assert.equal(result.ok, false);
  assert.equal(first.value, "FIRST");
  assert.match(result.message, /自动回滚未完成/);
  assert.deepEqual(revealCalls, []);
});

test("Ant virtual select clicks visible sibling options, never the accessibility mirror", async () => {
  let committed = "载货车";
  let mirrorClicks = 0;
  let foreignClicks = 0;
  const input = valueInput("");
  input.readOnly = true;
  input.getAttribute = name => ({ role: "combobox", "aria-controls": "truck_list" })[name] ?? null;
  const selected = { get textContent() { return committed; } };
  const actualOption = { textContent: "牵引车", getAttribute: () => null,
    click() { committed = "牵引车"; } };
  const dropdown = { querySelectorAll: () => [actualOption] };
  const accessibilityList = {
    closest: () => dropdown,
    querySelectorAll: () => [{ getAttribute: name => name === "aria-label" ? "牵引车" : null,
      click() { mirrorClicks++; } }],
  };
  const container = { querySelector: () => selected };
  input.closest = selector => selector === ".ant-select, .el-select" ? container : null;
  input.click = () => {};
  const root = {
    getElementById: id => id === "truck_list" ? accessibilityList : null,
    querySelectorAll: () => [{ textContent: "牵引车", click: () => foreignClicks++ }],
  };
  const result = await ReviewPageFieldWriter.executeValue(root, input,
    { field: "old_vehicle.type", value: "牵引车", expectedValue: "载货车" }, () => true);
  assert.equal(result.ok, true);
  assert.equal(committed, "牵引车");
  assert.equal(mirrorClicks, 0);
  assert.equal(foreignClicks, 0);
});

const classes = (...names) => ({ contains: name => names.includes(name) });

function calendarFixture({ initial = "2026-10-12", disabledDate = "", blockedNavigation = false, closingDelay = 0, stickyFirstSelection = false } = {}) {
  let committed = initial;
  let open = false;
  let closingUntil = 0;
  const close = () => { if (open) closingUntil = Date.now() + closingDelay; open = false; };
  const visible = () => (stickyFirstSelection && selectionClicks === 1) || open || Date.now() < closingUntil;
  let month = initial ? initial.slice(0, 7) : "2026-10";
  let navigations = 0;
  let selectionClicks = 0;
  const plain = { getAttribute: () => null, dispatchEvent() {} };
  const picker = {
    ...plain, classList: classes("ant-picker", ...(stickyFirstSelection ? ["ant-picker-focused"] : [])),
    querySelectorAll: () => [control],
    querySelector: () => ({ ...plain, click() {}, dispatchEvent(event) { if (event.type === "mouseup") committed = ""; } }),
  };
  const control = {
    ...plain, tagName: "INPUT", readOnly: true, isConnected: true,
    get value() { return committed; },
    set value(_) { throw new Error("readonly date must be committed by calendar events"); },
    closest: selector => selector.includes(".ant-picker") ? picker : null,
    focus() {}, blur() {}, click() {},
    dispatchEvent(event) {
      if (event.type === "mousedown") open = true;
      if (event.key === "Escape") close();
    },
  };
  const cells = () => Array.from({ length: new Date(`${month}-01T00:00:00Z`).getUTCMonth() === 1 ? 28 : 30 }, (_, index) => {
    const title = `${month}-${String(index + 1).padStart(2, "0")}`;
    return { ...plain,
      classList: classes("ant-picker-cell-in-view", ...(title === disabledDate ? ["ant-picker-cell-disabled"] : [])),
      getAttribute: name => name === "title" ? title : null,
      click() { selectionClicks++; committed = title; close(); },
    };
  });
  const panel = {
    ...plain,
    querySelectorAll: () => cells(),
    querySelector: selector => ({ ...plain, click() {
      navigations++;
      if (blockedNavigation) return;
      const delta = (selector.includes("super-") ? 12 : 1) * (selector.includes("prev") ? -1 : 1);
      const date = new Date(`${month}-01T00:00:00Z`);
      date.setUTCMonth(date.getUTCMonth() + delta);
      month = date.toISOString().slice(0, 7);
    } }),
  };
  const popup = {
    ...plain,
    get style() { return { display: visible() ? "block" : "none" }; },
    querySelectorAll: () => [panel],
    querySelector: selector => selector.includes("ant-picker-time-panel") ? null : cells()[0],
  };
  const root = {
    defaultView: {
      KeyboardEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
      MouseEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    },
    querySelectorAll: () => visible() ? [popup] : [],
  };
  control.ownerDocument = root;
  return { root, control, picker, value: () => committed, clicks: () => selectionClicks, navigations: () => navigations };
}

test("readonly Ant date input commits through its calendar and survives blur", async () => {
  const fixture = calendarFixture();
  const result = await ReviewPageFieldWriter.executeValue(fixture.root, fixture.control,
    { field: "old_vehicle.recycle_date", value: "2026-10-13", expectedValue: "2026-10-12" }, () => true);
  assert.equal(result.ok, true);
  assert.equal(fixture.value(), "2026-10-13");
  assert.equal(fixture.clicks(), 1);
  assert.equal(ReviewPageFieldWriter.controlKind(fixture.control), "date");
});

test("calendar selection navigates years and months before selecting the exact day", async () => {
  const fixture = calendarFixture();
  const result = await ReviewPageFieldWriter.executeValue(fixture.root, fixture.control,
    { field: "new_vehicle.registration_date", value: "2024-08-15", expectedValue: "2026-10-12" }, () => true);
  assert.equal(result.ok, true);
  assert.equal(fixture.value(), "2024-08-15");
  assert.equal(fixture.navigations(), 4);
});

test("vehicle license dates share calendar handling and reveal the visible picker without reopening it", async () => {
  for (const field of ["vehicle.registration_date", "vehicle.issue_date"]) {
    const fixture = calendarFixture();
    const presentation = [];
    fixture.picker.scrollIntoView = options => presentation.push(options.block);
    fixture.picker.animate = () => presentation.push("highlight");
    let focusCount = 0;
    fixture.control.focus = () => { focusCount++; assert.equal(fixture.value(), "2026-10-12"); };
    const result = await ReviewPageFieldWriter.executeValue(fixture.root, fixture.control,
      {field, value: "2024-08-15", expectedValue: "2026-10-12"}, () => true,
      {fields: new Set([field]), controlKinds: new Set(["date"])});
    assert.equal(result.ok, true, result.message);
    assert.equal(fixture.value(), "2024-08-15");
    assert.equal(fixture.clicks(), 1);
    assert.equal(focusCount, 1);
    assert.deepEqual(presentation, ["center", "highlight"]);
    assert.deepEqual(fixture.root.querySelectorAll(), []);
  }
});

test("consecutive date writes wait for the reused calendar's closing animation", async () => {
  const fixture = calendarFixture({ closingDelay: 180 });
  for (const [expectedValue, value] of [["2026-10-12", "2026-10-13"], ["2026-10-13", "2024-08-15"]]) {
    const result = await ReviewPageFieldWriter.executeValue(fixture.root, fixture.control,
      { field: "old_vehicle.recycle_date", value, expectedValue }, () => true);
    assert.equal(result.ok, true, result.message);
    assert.equal(fixture.value(), value);
  }
});

test("date validation, disabled cells, stale identity and blocked navigation never commit a date", async () => {
  for (const [value, options, guard] of [
    ["2026-02-30", {}, () => true],
    ["2026-10-13", { disabledDate: "2026-10-13" }, () => true],
    ["2026-11-13", { blockedNavigation: true }, () => true],
    ["2026-10-13", {}, () => false],
  ]) {
    const fixture = calendarFixture(options);
    const result = await ReviewPageFieldWriter.executeValue(fixture.root, fixture.control,
      { field: "old_vehicle.recycle_date", value, expectedValue: "2026-10-12" }, guard);
    assert.equal(result.ok, false);
    assert.equal(fixture.value(), "2026-10-12");
    assert.equal(fixture.clicks(), 0);
  }
  assert.equal(calendarDate("2024-02-29"), "2024-02-29");
  assert.equal(calendarDate("2024/2/29"), "2024-02-29");
  assert.equal(calendarDate("2025-02-29"), null);
});

test("OCR日期原文在共用回填入口归一化，报废和过户均能写入并读回", async () => {
  for (const field of ["old_vehicle.recycle_date", "invoice.invoice_date", "transfer_invoice.invoice_date", "transfer_new_invoice.invoice_date"]) {
    for (const value of ["2026年09月03日", "2026/9/3", "2026.09.03", "２０２６年 ９月 ３日"]) {
      const fixture = calendarFixture();
      const result = await ReviewPageFieldWriter.executeValue(fixture.root, fixture.control,
        {field, value, expectedValue: "2026年10月12日"}, () => true,
        {fields: new Set([field]), controlKinds: new Set(["date"])});
      assert.equal(result.ok, true, `${field} ${value}: ${result.message}`);
      assert.equal(fixture.value(), "2026-09-03");
      assert.equal(result.actions[0].value, "2026-09-03");
    }
  }
  for (const value of ["2026年02月30日", "2026/13/03", "09/03/2026", "2026-09/03", "2026-09-03至2026-09-08"]) {
    const fixture = calendarFixture();
    const result = await ReviewPageFieldWriter.executeValue(fixture.root, fixture.control,
      {field: "transfer_new_invoice.invoice_date", value}, () => true,
      {fields: new Set(["transfer_new_invoice.invoice_date"])});
    assert.equal(result.ok, false, value);
    assert.equal(fixture.clicks(), 0, value);
    assert.equal(fixture.value(), "2026-10-12");
  }
});

test("date picker is still subject to the backend control type allowlist", async () => {
  const fixture = calendarFixture();
  const result = await ReviewPageFieldWriter.executeValue(fixture.root, fixture.control,
    { field: "old_vehicle.recycle_date", value: "2026-10-13", expectedValue: "2026-10-12" }, () => true,
    { fields: new Set(["old_vehicle.recycle_date"]), controlKinds: new Set(["text"]) });
  assert.equal(result.ok, false);
  assert.match(result.message, /控件类型暂不支持/);
  assert.equal(fixture.clicks(), 0);
});

test("composite rollback clears a formerly empty Ant date using its mouseup handler", async () => {
  const fixture = calendarFixture({ initial: "" });
  const rejected = valueInput("ORIGINAL");
  rejected.dispatchEvent = () => { rejected.value = "FRAMEWORK-REJECTED"; };
  const result = await ReviewPageFieldWriter.executeValueGroup(fixture.root,
    [{ field: "old_vehicle.recycle_date", element: fixture.control }, { field: "new_vehicle.vin", element: rejected }],
    { fields: ["old_vehicle.recycle_date", "new_vehicle.vin"], value: "2026-10-13", expectedValues: { "old_vehicle.recycle_date": "", "new_vehicle.vin": "ORIGINAL" } }, () => true);
  assert.equal(result.ok, false);
  assert.equal(fixture.value(), "");
});

test("a calendar committed before its close failure is included in composite rollback", async () => {
  const fixture = calendarFixture({stickyFirstSelection: true});
  const other = valueInput("original");
  const result = await ReviewPageFieldWriter.executeValueGroup(fixture.root,
    [{field: "old_vehicle.recycle_date", element: fixture.control}, {field: "new_vehicle.vin", element: other}],
    {fields: ["old_vehicle.recycle_date", "new_vehicle.vin"], value: "2026-10-13", expectedValues: {}}, () => true);
  assert.equal(result.ok, false);
  assert.match(result.message, /日历未关闭.*已回滚/);
  assert.equal(fixture.value(), "2026-10-12");
  assert.equal(other.value, "original");
});


test("page writes serialize single and composite operations and release the lock", async () => {
  const root = {defaultView: {Event: class Event {}}};
  const first = valueInput("old-a"), second = valueInput("old-b");
  const writer = loadWriter({setTimeout});
  const pending = writer.executeValueGroup(root,
    [{field: "old_vehicle.vin", element: first}, {field: "new_vehicle.vin", element: second}],
    {fields: ["old_vehicle.vin", "new_vehicle.vin"], value: "new", expectedValues: {}},
  );
  const blocked = await writer.executeValue(root, second, {field: "new_vehicle.vin", value: "interleaved"});
  assert.equal(blocked.code, "WRITE_IN_PROGRESS");
  assert.equal((await pending).ok, true);
  assert.equal(first.value, "new");
  assert.equal(second.value, "new");
  assert.equal((await writer.executeValue(root, second, {field: "new_vehicle.vin", value: "after"})).ok, true);
  assert.equal(second.value, "after");
});

test("composite writes reject duplicated fields and aliased DOM controls before mutation", async () => {
  const root = {defaultView: {Event: class Event {}}};
  const first = valueInput("old-a"), second = valueInput("old-b");
  const writer = loadWriter({setTimeout});
  const action = {fields: ["old_vehicle.vin", "new_vehicle.vin"], value: "new", expectedValues: {}};
  for (const entries of [
    [{field: "old_vehicle.vin", element: first}, {field: "old_vehicle.vin", element: second}],
    [{field: "old_vehicle.vin", element: first}, {field: "new_vehicle.vin", element: first}],
  ]) {
    assert.equal((await writer.executeValueGroup(root, entries, action)).ok, false);
    assert.equal(first.value, "old-a");
    assert.equal(second.value, "old-b");
  }
});

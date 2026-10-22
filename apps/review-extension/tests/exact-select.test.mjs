import { onTestFinished } from "vitest";
import assert from "node:assert/strict";
import { test, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { inspectAntSelect } from "../src/platform/extension/ant-select-inspector.ts";
import { inspectExactOption } from "../src/platform/browser/actions/exact-select.ts";
import { ReviewPageFieldWriter } from "../src/platform/browser/actions/page-field-writer.ts";
import { reviewTabs } from "./helpers/review-tabs.mjs";

// Ant 4 的实际形状：自定义 fieldNames，选项 DOM 无 data-value，隐藏 listbox
// 无型号 ID，虚拟 Item key 是 rc-index-key-N。完整 ID 必须从实时扁平选项关联。
function fixture(t, virtual = false, fields = {value: "modelId", label: "modelName"}) {
  const f = reviewTabs();
  const globals = Object.fromEntries(["document", "location", "chrome"].map(key => [key, globalThis[key]]));
  onTestFinished(() => Object.assign(globalThis, globals));
  const add = (...args) => {
    const element = f.add(...args); element.isConnected = true;
    element.removeAttribute = name => element.attributes.delete(name);
    return element;
  };
  const app = add(f.document.body, "div", {"data-v-app": ""});
  const container = add(app, "div", {class: "ant-select"});
  const selected = add(container, "span", {class: "ant-select-selection-item"}, "同名销售车型");
  const control = add(container, "input", {role: "combobox", "aria-controls": "model_list", "aria-expanded": "true"});
  control.value = "";
  const popup = add(f.document.body, "div", {class: "ant-select-dropdown"});
  add(popup, "div", {id: "model_list", role: "listbox"});
  const holder = add(popup, "div", {class: "rc-virtual-list"});
  const options = [10, 11].map(id => ({[fields.value]: id, [fields.label]: "同名销售车型"}));
  const rows = options.map((data, index) => ({key: `rc-index-key-${index}`, value: data[fields.value], data}));
  const list = {props: {itemKey: "key", data: rows}, subTree: {el: holder, children: []}};
  const clicks = [], scrolls = [];
  const instance = {type: {name: "ASelect"}, props: {value: 10, options,
    fieldNames: fields}, subTree: {el: container, children: [{component: list}]},
    exposed: {scrollTo: ({key}) => { scrolls.push(key); render(rows.find(row => row.key === key)); }}};
  const nodes = new Map();
  function render(row) {
    if (nodes.has(row.value)) return;
    const element = add(holder, "div", {class: "ant-select-item-option"}, row.data[fields.label]);
    element.click = () => { clicks.push(row.value); instance.props.value = row.value; selected.textContent = row.data[fields.label]; };
    nodes.set(row.value, element);
    list.subTree.children.push({key: row.key, component: {subTree: {el: element}}});
  }
  render(rows[0]); if (!virtual) render(rows[1]);
  app._vnode = {component: {subTree: {children: [{component: instance}]}}};
  globalThis.document = f.document;
  globalThis.location = {href: "https://test.forjtruck.com/consistency-qingdao"};
  globalThis.chrome = {runtime: {sendMessage: async message => inspectAntSelect(message.token, message.expectedPageUrl, message.target)}};
  return {...f, control, instance, nodes, rows, list, clicks, scrolls, app, selected, add};
}

test.each([
  ["spec.brand", "id", "brandName"],
  ["spec.series", "seriesId", "seriesName"],
  ["vehicle.model", "modelId", "modelName"],
])("selects and verifies the exact ID for %s", async (field, value, label) => {
  const f = fixture(null, true, {value, label});
  const result = await ReviewPageFieldWriter.executeSequence(f.document,
    [{field, label: field, value: "同名销售车型", optionValue: "11"}], () => f.control, () => true,
    {fields: new Set([field]), inspectOption: inspectExactOption});
  assert.equal(result.ok, true, result.message);
  assert.equal(f.instance.props.value, 11);
  assert.deepEqual(f.clicks, [11]);
});

test("MAIN inspector serializes without closures and maps custom-field virtual keys to exact IDs", t => {
  const f = fixture(t, true);
  const inspect = new Function(`return (${inspectAntSelect.toString()})`)();
  f.control.setAttribute("data-review-select-token", "test-token");
  assert.deepEqual(inspect("test-token", location.href), {ok: true, selectedValue: "10", selectedLabel: "同名销售车型", valueField: "modelId"});
  const first = inspect("test-token", location.href, {id: "11", label: "同名销售车型", prepare: true});
  assert.equal(first.ok, true);
  assert.equal(first.optionToken, undefined);
  assert.deepEqual(f.scrolls, ["rc-index-key-1"]);
  const ready = inspect("test-token", location.href, {id: "11", label: "同名销售车型", prepare: true});
  assert.equal(f.nodes.get(11).getAttribute("data-review-option-token"), ready.optionToken);
  assert.equal(f.nodes.get(10).getAttribute("data-review-option-token"), null);
  assert.deepEqual(f.clicks, [], "inspector never chooses an option or writes business state");
  assert.equal(f.app.__vueParentComponent, undefined, "production root vnode works without development expandos");
});

test("parallel series loading and automatic search cannot overwrite one control's inspection token", async t => {
  const f = fixture(t);
  globalThis.chrome.runtime.sendMessage = async message => {
    await new Promise(setImmediate);
    return inspectAntSelect(message.token, message.expectedPageUrl, message.target);
  };
  const reads = await Promise.allSettled([inspectExactOption(f.control), inspectExactOption(f.control)]);
  assert.deepEqual(reads.map(read => read.status), ["fulfilled", "fulfilled"]);
  assert.ok(reads.every(read => read.value.selectedValue === "10"));
  assert.equal(f.control.getAttribute("data-review-select-token"), null);
});

test("a failed scope inspection does not strand the next queued read", async t => {
  const f = fixture(t);
  let calls = 0;
  globalThis.chrome.runtime.sendMessage = async message => {
    await new Promise(setImmediate);
    if (++calls === 1) throw new Error("inspection transport disconnected");
    return inspectAntSelect(message.token, message.expectedPageUrl, message.target);
  };
  const reads = await Promise.allSettled([inspectExactOption(f.control), inspectExactOption(f.control)]);
  assert.equal(reads[0].status, "rejected");
  assert.equal(reads[1].status, "fulfilled");
  assert.equal(reads[1].value.selectedValue, "10");
  assert.equal(f.control.getAttribute("data-review-select-token"), null);
});

test("failed model linkage stops dependent writes and releases observation resources", async t => {
  const f = fixture(t, true);
  const hp = f.add(f.form, "input"); hp.value = "OLD";
  let disposed = false;
  const result = await ReviewPageFieldWriter.executeSequence(f.document, [
    {field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "11"},
    {field: "spec.max_hp", label: "最大马力", value: "450"},
  ], field => field === "vehicle.model" ? f.control : hp, () => true, {
    fields: new Set(["vehicle.model", "spec.max_hp"]), inspectOption: inspectExactOption,
    observeWrite: item => {
      assert.equal(item.field, "vehicle.model");
      assert.equal(f.instance.props.value, 10, "observation starts before the model selection");
      return {wait: async () => {
        assert.equal(f.instance.props.value, 11);
        return {ok: false, code: "MODEL_LINKAGE_FAILED", message: "配置加载失败"};
      }, dispose: () => {disposed = true;}};
    },
  });
  assert.equal(result.ok, false);
  assert.equal(result.code, "MODEL_LINKAGE_FAILED");
  assert.equal(hp.value, "OLD");
  assert.equal(result.actions[0].status, "FILLED", "confirmed parent action remains available for identity validation");
  assert.equal(disposed, true);
});

test("cached series selection retains actual ID and displayed label after remote options change", t => {
  const f = fixture(t, false, {value: "seriesId", label: "seriesName"});
  f.instance.props.options = [];
  f.selected.textContent = "欧曼GTL";
  f.control.setAttribute("data-review-select-token", "cached");
  const result = inspectAntSelect("cached", location.href);
  assert.deepEqual(result, {ok: true, selectedValue: "10", selectedLabel: "欧曼GTL", valueField: "seriesId"});
  assert.equal(inspectAntSelect("cached", location.href, {id: "10", label: "欧曼GTL", prepare: true}).ok, false);
  assert.deepEqual(f.clicks, []);
});

test("duplicate business IDs can be read but ambiguous target options cannot be written", t => {
  const f = fixture(t, false, {value: "seriesId", label: "seriesName"});
  f.instance.props.options.push({...f.instance.props.options[0]});
  f.control.setAttribute("data-review-select-token", "duplicates");
  assert.equal(inspectAntSelect("duplicates", location.href).selectedValue, "10");
  assert.equal(inspectAntSelect("duplicates", location.href, {id: "10", label: "同名销售车型", prepare: true}).ok, false);
  f.selected.textContent = "与选中 ID 无关的车系";
  assert.equal(inspectAntSelect("duplicates", location.href).ok, false);
});

test("same current label with another ID is clicked and read back by ID through the full sequence", async t => {
  const f = fixture(t, true);
  const values = [{field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "11"}];
  const result = await ReviewPageFieldWriter.executeSequence(f.document, values, () => f.control, () => true,
    {fields: new Set(["vehicle.model"]), inspectOption: inspectExactOption});
  assert.equal(result.ok, true, result.message);
  assert.deepEqual(f.clicks, [11]);
  assert.equal(f.instance.props.value, 11);
  assert.equal(result.actions[0].status, "FILLED");
  assert.equal(f.control.getAttribute("data-review-select-token"), null);
  assert.equal(f.nodes.get(11).getAttribute("data-review-option-token"), null);
});

test("HTTP pages without randomUUID still select and verify the exact model ID", async t => {
  const f = fixture(t, true);
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  onTestFinished(() => Object.defineProperty(globalThis, "crypto", descriptor));
  Object.defineProperty(globalThis, "crypto", {configurable: true,
    value: {getRandomValues: bytes => webcrypto.getRandomValues(bytes)}});
  globalThis.location.href = "http://test.forjtruck.com/consistency-qingdao?showPageModel=1";
  const tokens = [];
  globalThis.chrome.runtime.sendMessage = async message => {
    assert.match(message.token, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
    tokens.push(message.token);
    return inspectAntSelect(message.token, message.expectedPageUrl, message.target);
  };
  const result = await ReviewPageFieldWriter.executeSequence(f.document,
    [{field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "11"}],
    () => f.control, () => true, {fields: new Set(["vehicle.model"]), inspectOption: inspectExactOption});
  assert.equal(result.ok, true, result.message);
  assert.equal(f.instance.props.value, 11);
  assert.deepEqual(f.clicks, [11]);
  assert.ok(tokens.length > 2);
  assert.equal(new Set(tokens).size, tokens.length);
  assert.equal(f.control.getAttribute("data-review-select-token"), null);
});

test("successful group fill centers the visible model selector and highlights even unchanged controls", async t => {
  const f = fixture(t);
  const selector = f.control.closest(".ant-select");
  const hp = f.add(f.form, "input"); hp.value = "450";
  const calls = [];
  selector.scrollIntoView = () => calls.push("center-model");
  selector.animate = () => calls.push("highlight-model");
  f.control.scrollIntoView = () => assert.fail("must scroll the visible selector, not its hidden search input");
  f.control.focus = () => assert.fail("must not reopen a committed selector");
  hp.scrollIntoView = () => calls.push("center-hp");
  hp.animate = () => calls.push("highlight-hp");
  const values = [{field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "11"},
    {field: "spec.max_hp", label: "最大马力", value: "450"}];
  const result = await ReviewPageFieldWriter.executeSequence(f.document, values,
    field => field === "vehicle.model" ? f.control : hp, () => true,
    {fields: new Set(values.map(item => item.field)), inspectOption: inspectExactOption});
  assert.equal(result.ok, true, result.message);
  assert.equal(result.actions[1].status, "UNCHANGED");
  assert.deepEqual(calls.slice(-3), ["center-model", "highlight-model", "highlight-hp"]);
  assert.ok(!calls.includes("center-hp"));
});

test("matching text cannot conceal selecting a different ID, and restoration uses original ID", async t => {
  const f = fixture(t);
  f.nodes.get(11).click = () => { f.clicks.push("wrong"); f.instance.props.value = 999; };
  const result = await ReviewPageFieldWriter.executeValue(f.document, f.control,
    {field: "vehicle.model", value: "同名销售车型", optionValue: "11"}, () => true,
    {fields: new Set(["vehicle.model"]), inspectOption: inspectExactOption});
  assert.equal(result.ok, false);
  assert.match(result.message, /回读失败/);
  assert.deepEqual(f.clicks, ["wrong", 10]);
  assert.equal(f.instance.props.value, 10);
});

test("an identity change during MAIN-world inspection prevents the DOM click", async t => {
  const f = fixture(t);
  let current = true;
  const inspect = async (...args) => {
    const result = await inspectExactOption(...args);
    if (args[1]) current = false;
    return result;
  };
  const result = await ReviewPageFieldWriter.executeValue(f.document, f.control,
    {field: "vehicle.model", value: "同名销售车型", optionValue: "11"}, () => current,
    {fields: new Set(["vehicle.model"]), inspectOption: inspect});
  assert.equal(result.ok, false);
  assert.deepEqual(f.clicks, []);
});

test("the final group readback rejects a later ID overwrite even when the visible model name stays unchanged", async t => {
  const f = fixture(t);
  const hp = f.add(f.form, "input"); hp.value = "300";
  hp.dispatchEvent = () => { f.instance.props.value = 10; };
  const values = [{field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "11"},
    {field: "spec.max_hp", label: "最大马力", value: "450"}];
  const result = await ReviewPageFieldWriter.executeSequence(f.document, values,
    field => field === "vehicle.model" ? f.control : hp, () => true,
    {fields: new Set(values.map(item => item.field)), inspectOption: inspectExactOption});
  assert.equal(result.ok, false);
  assert.match(result.message, /车型最终回读不一致/);
});

test("a same-name option in a foreign popup is never selected", async t => {
  const f = fixture(t);
  const foreignPopup = f.add(f.document.body, "div", {class: "ant-select-dropdown"});
  const foreignOption = f.add(foreignPopup, "div", {class: "ant-select-item-option", "data-value": "11"}, "同名销售车型");
  foreignOption.click = () => assert.fail("clicked a foreign popup");
  const result = await ReviewPageFieldWriter.executeValue(f.document, f.control,
    {field: "vehicle.model", value: "同名销售车型", optionValue: "11"}, () => true,
    {fields: new Set(["vehicle.model"]), inspectOption: inspectExactOption});
  assert.equal(result.ok, true, result.message);
  assert.deepEqual(f.clicks, [11]);
});

test("invalid target IDs, disabled options and changed pages never fall back to matching names", async t => {
  const f = fixture(t);
  f.control.setAttribute("data-review-select-token", "test-token");
  for (const target of [{id: "99", label: "同名销售车型", prepare: true}, {id: "11", label: "别的名字", prepare: true}]) {
    assert.equal(inspectAntSelect("test-token", location.href, target).ok, false);
  }
  f.instance.props.options[1].disabled = true;
  assert.equal(inspectAntSelect("test-token", location.href, {id: "11", label: "同名销售车型", prepare: true}).ok, false);
  assert.equal(inspectAntSelect("test-token", location.href + "?other").ok, false);
  f.instance.props.options[1].disabled = false;
  const result = await ReviewPageFieldWriter.executeValue(f.document, f.control,
    {field: "vehicle.model", value: "同名销售车型", optionValue: "11"}, () => true, {fields: new Set(["vehicle.model"])});
  assert.equal(result.ok, false);
  assert.deepEqual(f.clicks, []);
});


test("live label resolution preserves aliases for the same verified ID", async t => {
  const f = fixture(t);
  const values = [{field: "spec.series", label: "车系", value: "目录别名", optionValue: "10"}];
  const result = await ReviewPageFieldWriter.executeSequence(f.document, values, () => f.control, () => true,
    {fields: new Set(["spec.series"]), inspectOption: inspectExactOption, useLiveOptionLabels: true});
  assert.equal(result.ok, true, result.message);
  assert.deepEqual(f.clicks, []);
  assert.equal(values[0].value, "同名销售车型");
});

test("unchanged cached selections do not require remote options or click their parents", async t => {
  const f = fixture(t, false, {value: "seriesId", label: "seriesName"});
  f.instance.props.options = [];
  const values = [{field: "spec.series", label: "车系", value: "目录别名", optionValue: "10"}];
  const result = await ReviewPageFieldWriter.executeSequence(f.document, values, () => f.control, () => true,
    {fields: new Set(["spec.series"]), inspectOption: inspectExactOption, useLiveOptionLabels: true,
      optionValueFields: {"spec.series": ["seriesId"]}});
  assert.equal(result.ok, true, result.message);
  assert.equal(result.actions[0].status, "UNCHANGED");
  assert.equal(values[0].value, "同名销售车型");
  assert.deepEqual(f.clicks, []);
});

test("duplicate options for an already-selected ID do not block an unchanged field", async t => {
  const f = fixture(t);
  f.instance.props.options.push({...f.instance.props.options[0]});
  const result = await ReviewPageFieldWriter.executeSequence(f.document,
    [{field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "10"}], () => f.control, () => true,
    {fields: new Set(["vehicle.model"]), inspectOption: inspectExactOption, useLiveOptionLabels: true});
  assert.equal(result.ok, true, result.message);
  assert.deepEqual(f.clicks, []);
});

test("a different same-label model opens the dropdown and waits for lazy target options", async t => {
  const f = fixture(t);
  f.instance.props.options = [f.rows[0].data];
  f.control.setAttribute("aria-expanded", "false");
  const opens = [];
  f.control.click = () => {
    opens.push("open"); f.control.setAttribute("aria-expanded", "true");
    f.instance.props.options = f.rows.map(row => row.data);
  };
  const result = await ReviewPageFieldWriter.executeSequence(f.document,
    [{field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "11"}], () => f.control, () => true,
    {fields: new Set(["vehicle.model"]), inspectOption: inspectExactOption, useLiveOptionLabels: true});
  assert.equal(result.ok, true, result.message);
  assert.deepEqual(opens, ["open"]);
  assert.deepEqual(f.clicks, [11]);
  assert.equal(f.instance.props.value, 11);
});

test.each(["disabled", "duplicate"])("a %s target reports the specific field and never writes subsequent parameters", async kind => {
  const f = fixture(null);
  if (kind === "disabled") f.instance.props.options[1].disabled = true;
  else f.instance.props.options.push({...f.instance.props.options[1]});
  const hp = f.add(f.form, "input"); hp.value = "420";
  const result = await ReviewPageFieldWriter.executeSequence(f.document,
    [{field: "spec.brand", label: "品牌", value: "同名销售车型", optionValue: "11"},
      {field: "spec.max_hp", label: "马力", value: "450"}], field => field === "spec.brand" ? f.control : hp, () => true,
    {fields: new Set(["spec.brand", "spec.max_hp"]), inspectOption: inspectExactOption, useLiveOptionLabels: true});
  assert.equal(result.ok, false);
  assert.equal(result.code, kind === "disabled" ? "SELECT_OPTION_DISABLED" : "SELECT_OPTION_DUPLICATE");
  assert.match(result.message, /^品牌回填预检失败/);
  assert.equal(hp.value, "420");
  assert.deepEqual(f.clicks, []);
});

test("identity changes while waiting for options stop without clicking", async t => {
  const f = fixture(t);
  f.instance.props.options = [f.rows[0].data];
  f.control.setAttribute("aria-expanded", "false");
  let current = true;
  f.control.click = () => { f.control.setAttribute("aria-expanded", "true"); current = false; };
  const result = await ReviewPageFieldWriter.executeSequence(f.document,
    [{field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "11"}], () => f.control, () => current,
    {fields: new Set(["vehicle.model"]), inspectOption: inspectExactOption, useLiveOptionLabels: true});
  assert.equal(result.ok, false);
  assert.equal(result.code, "PAGE_IDENTITY_CHANGED");
  assert.deepEqual(f.clicks, []);
});

test("a genuinely missing target times out with field and ID diagnostics, without falling back to its name", async t => {
  const f = fixture(t);
  vi.useFakeTimers(); onTestFinished(() => vi.useRealTimers());
  f.instance.props.options = [f.rows[0].data];
  const pending = ReviewPageFieldWriter.executeSequence(f.document,
    [{field: "vehicle.model", label: "车型", value: "同名销售车型", optionValue: "11"}], () => f.control, () => true,
    {fields: new Set(["vehicle.model"]), inspectOption: inspectExactOption, useLiveOptionLabels: true});
  await vi.runAllTimersAsync();
  const result = await pending;
  assert.equal(result.ok, false);
  assert.equal(result.code, "SELECT_OPTION_MISSING");
  assert.match(result.message, /^车型回填预检失败：.*目标 ID 11.*已加载 1 项/);
  assert.deepEqual(f.clicks, []);
  assert.equal(f.instance.props.value, 10);
});


test("a remounted child with internal series ID semantics is blocked before clicking", async t => {
  const f = fixture(t, false, {value: "id", label: "brandName"});
  const result = await ReviewPageFieldWriter.executeSequence(f.document,
    [{field: "spec.series", label: "车系", value: "目录别名", optionValue: "11"}], () => f.control, () => true,
    {fields: new Set(["spec.series"]), inspectOption: inspectExactOption, useLiveOptionLabels: true,
      optionValueFields: {"spec.series": ["seriesId"]}});
  assert.equal(result.ok, false);
  assert.match(result.message, /字段语义已变化/);
  assert.deepEqual(f.clicks, []);
});

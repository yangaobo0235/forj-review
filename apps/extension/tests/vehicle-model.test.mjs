import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRenderer, effectScope, h, nextTick, reactive, ref } from "vue";
import { renderToString } from "vue/server-renderer";
import { loadSource } from "./vue-loader.mjs";
import { applyCollectManifest, manifestIgnoresImage } from "../src/platform/browser/catalog/collect-manifest.ts";
import { ReviewBusinessScope } from "../src/platform/browser/collection/business-scope.ts";
import { ReviewImageCandidates } from "../src/platform/browser/collection/image-candidates.ts";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import { modelFillPlan, normalizeEngineCode, sameModelData, validatedModelInput, verifyModelOption }
  from "../src/shared/vehicle-model.ts";
import { requestModelCatalog } from "../src/platform/extension/vehicle-model-server.ts";
import { queryVehicleModels, fillVehicleModel, vehicleModelMessage, searchVehicleModels } from "../src/platform/extension/vehicle-model-client.ts";
import { ReviewPageFieldWriter } from "../src/platform/browser/actions/page-field-writer.ts";

const input = {announcementModel: "MODEL100A80", engineModel: "ENGINE42-E6",
  pageScope: {brandName: "示例品牌", seriesName: "示例车系", typeLevel: "牵引车"}};
const candidate = () => ({modelId: "10", modelName: "示例品牌 完整车型名称 420马力 6X4 手动版 (MODEL100A80)",
  brandId: "2", brandName: "示例品牌", seriesId: "3", seriesName: "示例车系", sameNameCount: 1,
  warnings: [], config: {driveForm: "6X4", fuelType: "柴油", emissionStandard: "国六", maxHp: "420",
    engineBrand: "示例发动机", engineType: "ENGINE42-E6", packingCaseLength: null}});

const materialCheck = (announcement = input.announcementModel, engine = input.engineModel) => ({
  check_id: "VEHICLE-SOURCE-MODEL", details: {model_search: {announcement_model: announcement, engine_model: engine, issues: []}},
});
// 加载器在 VM 中执行源码，等待跨 realm Promise 及 Vue 更新全部落定。
async function settle() { await new Promise(setImmediate); await nextTick(); }
function modelHarness(t, query = async () => ({ok: true, candidates: [candidate()], searchId: "search-1"}), extra = {}) {
  const {useVehicleModelReview} = loadSource("features/review/model/use-vehicle-model-review.ts");
  const props = reactive({review: {business_type: "vehicle_source", cross_checks: [materialCheck()]},
    pageData: {sourceTabId: 12, pageUrl: "https://example.test/consistency", pageInstanceId: "instance-1",
      pageFingerprint: "record-1", collectionId: "collection-1", pageFields: {"vehicle.model": "页面原车型", "spec.brand": "示例品牌", "spec.series": "示例车系", "vehicle.type": "牵引车"}}, disabled: false, ...extra});
  const calls = [], results = [], fills = [], busy = [];
  const scope = effectScope();
  const model = scope.run(() => useVehicleModelReview({
    review: () => props.review, pageData: () => props.pageData, disabled: () => props.disabled,
    onBusy: value => busy.push(value), onResult: result => results.push(result),
    onFill: () => async (...args) => { fills.push(args); return props.fillResponse || {ok: true, message: "规格参数已回填并回读"}; },
  }, (...args) => { calls.push(args); return query(...args); }));
  t.after(() => scope.stop());
  return {props, model, calls, results, fills, busy, scope};
}

test("new source manifest collects spec controls, excludes supplementary cards and vehicle photos", () => {
  const manifest = JSON.parse(readFileSync(new URL("./fixtures/collect-manifest-vehicle-source.json", import.meta.url), "utf8"));
  applyCollectManifest(manifest);
  try {
    assert.equal(ReviewPageFieldCollector.definitionForLabel("发动机号").field, "vehicle.engine_no");
    assert.equal(ReviewPageFieldCollector.definitionForLabel("发动机型号").field, "spec.engine_model");
    assert.equal(ReviewPageFieldCollector.definitionForLabel("驱动形式").reviewable, false);
    assert.equal(ReviewPageFieldCollector.definitionForLabel("使用性质"), null);
    assert.equal(ReviewBusinessScope.scopeForLabel("证件资料").scope, "vehicle");
    assert.equal(ReviewBusinessScope.scopeForLabel("车况资料").scope, "other");
    assert.equal(ReviewBusinessScope.scopeForLabel("附件").scope, "other");
    assert.equal(manifestIgnoresImage("登记证书3、 4页"), true);
    assert.equal(manifestIgnoresImage("行驶证反面"), true);
    assert.equal(manifestIgnoresImage("登记证书1、2页"), false);
    assert.equal(manifestIgnoresImage("登记证书3、4页 铭牌"), false);
    const items = ["行驶证正面", "登记证书3、4页", "铭牌"].map((label, index) => ({index,
      src: `https://example.test/${index}.jpg`, visible: true, businessScope: "vehicle", categoryHint: "vehicle",
      ignoredMaterial: manifestIgnoresImage(label)}));
    assert.deepEqual(ReviewImageCandidates.select(items).selected.map(item => item.index), [0, 2]);
  } finally { applyCollectManifest(null); }
});

test("model picker shows permanent two-row inputs and a visible disabled fill button before selection", async t => {
  const {default: Picker} = loadSource("features/review/ui/VehicleModelPicker.vue");
  const {model, props} = modelHarness(t);
  await settle();
  model.source.issues.push("请确认辅助材料");
  const html = await renderToString(h(Picker, {disabled: false, model, pageData: props.pageData}));
  assert.match(html, /MODEL100A80/);
  assert.match(html, /ENGINE42-E6/);
  assert.match(html, /<form/);
  assert.match(html, /请确认辅助材料/);
  assert.match(html, /<button[^>]*disabled[^>]*>回填整个规格参数<\/button>/);
  assert.match(html, /请先选择候选车型/);
  assert.doesNotMatch(html, /checked|<article|<h2|修改搜索条件|候选车型分页|变速箱未提供|换挡形式未提供/);
  assert.match(html, /搜索车型/);
  assert.equal((html.match(/<input[^>]*maxlength="80"/g) || []).length, 2);
});

test("model auto-search waits for both material codes and enabled review, then queries once", async t => {
  const harness = modelHarness(t, undefined, {disabled: true,
    review: {business_type: "vehicle_source", cross_checks: [materialCheck(input.announcementModel, "")]}});
  const {model, props, calls} = harness;
  assert.equal(calls.length, 0);
  props.review.cross_checks = [materialCheck()];
  assert.equal(calls.length, 0);
  props.disabled = false;
  assert.equal(calls.length, 1);
  await settle();
  assert.equal(model.state.status, "FOUND");
  assert.equal(model.state.selectedId, ""); // 一个候选也不自动选择。
  props.review.cross_checks = [materialCheck(" model100a80 ", " engine42-e6 ")];
  props.disabled = true; props.disabled = false;
  await settle();
  assert.equal(calls.length, 1);
  model.state.selectedId = "10";
  assert.equal(model.fillDisabledReason, "");
});

test("page scope is required, sent with both codes, displayed and changing it invalidates old choices", async t => {
  const {model, props, calls} = modelHarness(t);
  await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1].pageScope)), input.pageScope);
  model.select("10");
  props.pageData.pageFields["spec.series"] = "其他车系";
  assert.equal(model.state.selectedId, "");
  assert.equal(model.state.candidates.length, 0);
  await settle();
  assert.equal(calls.length, 2);
  assert.equal(calls[1][1].pageScope.seriesName, "其他车系");
  const {default: Picker} = loadSource("features/review/ui/VehicleModelPicker.vue");
  const html = await renderToString(h(Picker, {disabled: false, model, pageData: props.pageData}));
  assert.match(html, /查询范围：示例品牌 \/ 其他车系 \/ 牵引车/);
  props.pageData.pageFields["vehicle.type"] = "";
  await settle();
  assert.equal(model.scopeReady, false);
  assert.equal(model.state.candidates.length, 0);
  assert.match(model.state.message, /缺少页面品牌、车系或车辆类型/);
  await model.search();
  assert.equal(calls.length, 2);
  props.pageData.pageFields["vehicle.type"] = "牵引车";
  await settle();
  assert.equal(calls.length, 3);
});

test("raw dropdown IDs cannot silently become a successful empty catalog search", async t => {
  const {model, calls} = modelHarness(t, undefined, {pageData: {pageFields: {
    "spec.brand": "东风商用车", "spec.series": "23", "vehicle.type": "牵引车", "vehicle.model": "34922"}}});
  await settle();
  assert.equal(calls.length, 0);
  assert.equal(model.scopeReady, false);
  assert.match(model.state.message, /内部编号.*选中名称/);
  await model.search();
  assert.equal(calls.length, 0);
  assert.equal(model.state.status, "ERROR");
  assert.match(model.state.message, /内部编号/);
});

test("manual input invalidates old choices and survives later OCR, with explicit material reset", async t => {
  const {model, props, calls} = modelHarness(t);
  await settle();
  model.state.selectedId = "10";
  model.edit("engineModel", "ENGINE50-E6");
  assert.equal(model.state.selectedId, "");
  assert.equal(model.state.searchId, "");
  assert.equal(model.state.candidates.length, 0);
  props.review.cross_checks = [materialCheck("MODEL200A", "ENGINE60-E6")];
  assert.equal(model.state.input.engineModel, "ENGINE50-E6");
  assert.equal(calls.length, 1);
  await model.search();
  assert.equal(calls[1][1].engineModel, "ENGINE50-E6");
  model.useMaterials();
  await settle();
  assert.equal(calls[2][1].engineModel, "ENGINE60-E6");
  assert.equal(model.state.manual, false);
  model.select("10");
  props.disabled = true; props.disabled = false;
  assert.equal(model.state.searchId, "search-1");
  assert.equal(model.state.selectedId, "10");
  assert.equal(calls.length, 3);
});

test("switching actual field details preserves model candidates, choice and permanent search inputs", async t => {
  const {model, props, calls} = modelHarness(t);
  const {default: Detail} = loadSource("features/review/ui/ReviewStepDetail.vue");
  const {default: Picker} = loadSource("features/review/ui/VehicleModelPicker.vue");
  const field = ref("vehicle.vin");
  const node = (type, text = "") => ({type, text, children: [], props: {}, querySelector: () => null,
    addEventListener() {}, removeEventListener() {}, ownerDocument: {activeElement: null}, getRootNode: () => ({activeElement: null}), getAttribute: () => null});
  const renderer = createRenderer({
    createElement: node, createText: text => node("text", text), createComment: text => node("comment", text),
    setText: (el, text) => { el.text = text; }, setElementText: (el, text) => { el.text = text; el.children = []; },
    patchProp: (el, key, _old, value) => { el.props[key] = value; },
    insert: (child, parent, anchor) => {
      if (child.parent) child.parent.children = child.parent.children.filter(item => item !== child);
      child.parent = parent;
      const index = anchor ? parent.children.indexOf(anchor) : -1;
      if (index < 0) parent.children.push(child); else parent.children.splice(index, 0, child);
    },
    remove: el => { el.parent.children = el.parent.children.filter(item => item !== el); },
    parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] || null,
  });
  const app = renderer.createApp({setup: () => () => h(Detail, {
    step: {step_id: `FIELD-${field.value}`, category: "FIELD", label: field.value === "vehicle.model" ? "车型" : "VIN",
      page_target_field: field.value, result_status: "INSUFFICIENT", values: [], evidence: [], writable: true},
    customFieldReview: field.value === "vehicle.model", pageData: props.pageData, qrChecks: [], disabled: false,
    onFocusImage: async () => {}, onFill: async () => {}, onChoose: () => {},
  }, field.value === "vehicle.model" ? {"field-review": () => h(Picker, {model, pageData: props.pageData, disabled: false})} : {})});
  const root = node("root");
  app.mount(root); t.after(() => app.unmount());
  await settle();
  assert.equal(calls.length, 1); // 当前停留 VIN，搜索已完成。
  const find = (el, predicate) => predicate(el) ? el : el.children.map(child => find(child, predicate)).find(Boolean);
  assert.equal(find(root, el => el.props.class === "model-picker"), undefined);
  field.value = "vehicle.model";
  await settle();
  find(root, el => el.type === "input" && el.props.type === "radio").props.onChange();
  await settle();
  assert.equal(model.state.selectedId, "10");
  field.value = "vehicle.vin"; await settle();
  assert.equal(find(root, el => el.props.class === "model-picker"), undefined);
  field.value = "vehicle.model"; await settle();
  assert.equal(find(root, el => el.type === "input" && el.props.type === "radio").props.checked, true);
  assert.ok(find(root, el => el.type === "form"));
  assert.equal(calls.length, 1);
});

test("workbench embeds model search inside the standard field card with source images and no direct code fill", async () => {
  const {default: Workbench} = loadSource("features/review/ui/ReviewWorkbench.vue");
  const html = await renderToString(h(Workbench, {readOnly: true,
    onFocusImage: async () => ({ok: true}), onApplyPageFieldValue: async () => ({ok: true, message: ""}),
    onApplyPageFieldGroupValue: async () => ({ok: true, message: ""}),
    review: {business_type: "vehicle_source", qr_checks: [], cross_checks: [materialCheck()], review_tasks: [{
      step_id: "FIELD-vehicle.model", category: "FIELD", label: "车型", sequence: 1,
      page_target_field: "vehicle.model", result_status: "INSUFFICIENT", requires_reviewer_action: true, writable: true,
      values: [{source: "铭牌", value: input.announcementModel, image_id: "nameplate"}], evidence: [],
    }]}, pageData: {pageFields: {"vehicle.model": "页面原车型"}, images: [{imageId: "nameplate", src: "https://example.test/nameplate.jpg"}]},
  }));
  assert.equal((html.match(/<article class="workbench-card"/g) || []).length, 1);
  assert.match(html, /页面原车型/);
  assert.match(html, /查看原图/);
  assert.match(html, /匹配候选车型/);
  assert.match(html, /回填整个规格参数/);
  assert.match(html, /标记人工复核/);
  assert.doesNotMatch(html, /manual-fill|回填此值/);
});

test("late searches cannot restore candidates after editing, changing records, or disposal", async t => {
  const resolvers = [];
  const {model, props, scope} = modelHarness(t, () => new Promise(resolve => resolvers.push(resolve)));
  model.edit("engineModel", "OTHER42-E6");
  resolvers[0]({ok: true, candidates: [candidate()], searchId: "obsolete-edit"});
  await settle();
  assert.equal(model.state.candidates.length, 0);
  const manual = model.search();
  props.pageData = {...props.pageData, pageFingerprint: "record-2", collectionId: "collection-2"};
  resolvers[1]({ok: true, candidates: [candidate()], searchId: "obsolete-record"});
  await manual;
  assert.equal(model.state.selectedId, "");
  assert.equal(model.state.manual, false);
  assert.equal(model.state.searchId, "");
  scope.stop();
  resolvers[2]({ok: true, candidates: [candidate()], searchId: "obsolete-unmount"});
  await settle();
  assert.equal(model.state.candidates.length, 0);
});

test("connection failures remain recoverable errors rather than empty results or automatic retries", async t => {
  let fail = true;
  const {model, props, calls} = modelHarness(t, async () => {
    if (fail) return {ok: false, message: "Could not establish connection. Receiving end does not exist.", candidates: []};
    return {ok: true, searchId: "retry", candidates: []};
  });
  await settle();
  assert.equal(model.state.status, "ERROR");
  assert.match(model.state.message, /连接已失效/);
  assert.doesNotMatch(model.state.message, /Receiving end|未找到/);
  props.disabled = true; props.disabled = false;
  assert.equal(calls.length, 1);
  fail = false;
  await model.search();
  assert.equal(model.state.status, "EMPTY");
  assert.match(model.state.message, /未找到/);
  assert.equal(calls.length, 2);
});

test("selected model previews the whole spec group and fills only on explicit action", async t => {
  const {model, props, fills, results, busy} = modelHarness(t);
  await settle();
  await model.fill();
  assert.equal(fills.length, 0);
  model.state.selectedId = "10";
  const {default: Picker} = loadSource("features/review/ui/VehicleModelPicker.vue");
  const html = await renderToString(h(Picker, {disabled: false, model, pageData: props.pageData}));
  assert.match(html, /回填预览：将更新 7 项参数/);
  assert.match(html, /<button(?![^>]*disabled)[^>]*>回填整个规格参数<\/button>/);
  await model.fill();
  assert.deepEqual(fills, [["search-1", "10"]]);
  assert.deepEqual(busy, [true, false]);
  assert.equal(results[0].ok, true);
  model.state.candidates[0].sameNameCount = 2;
  assert.equal(model.fillDisabledReason, "");
  await model.fill();
  assert.equal(fills.length, 2);
  model.state.candidates[0].sameNameCount = 1;
  model.state.candidates[0].config.fuelType = null;
  assert.match(model.fillDisabledReason, /燃料种类/);
});

test("model search errors provide Chinese messages while retaining actionable configuration errors", () => {
  assert.match(vehicleModelMessage(new Error("Failed to fetch")), /审核服务连接失败/);
  assert.match(vehicleModelMessage(new Error("Extension context invalidated.")), /页面或扩展连接已失效/);
  assert.match(vehicleModelMessage(new Error("No document with id: old-document")), /刷新业务页面并重新读取/);
  assert.equal(vehicleModelMessage("正式车型库尚未配置"), "正式车型库尚未配置");
});

test("a genuinely replaced search clears stale candidates instead of leaving an apparently fillable choice", async t => {
  const {model, fills} = modelHarness(t, undefined, {fillResponse: {ok: false,
    code: "MODEL_SEARCH_CHANGED", message: "已有更新的搜索，请重新搜索并选择"}});
  await settle(); model.select("10");
  await model.fill();
  assert.equal(model.state.status, "ERROR");
  assert.equal(model.state.selectedId, "");
  assert.equal(model.state.searchId, "");
  assert.equal(model.state.candidates.length, 0);
  assert.match(model.operation.fillMessage, /重新搜索/);
  await model.fill();
  assert.equal(fills.length, 1);
  await model.search();
  model.select("10");
  assert.equal(model.fillDisabledReason, "");
});

test("an in-progress fill locks search controls and cannot publish success to a replacement record", async t => {
  let finish;
  const fillResponse = new Promise(resolve => {finish = resolve;});
  const {model, props, results, calls, busy} = modelHarness(t, undefined, {fillResponse});
  await settle(); model.select("10");
  const filling = model.fill();
  assert.equal(model.operation.filling, true);
  model.edit("engineModel", "OTHER42-E6");
  await model.search();
  assert.equal(model.state.input.engineModel, input.engineModel);
  assert.equal(calls.length, 1);
  props.pageData = {...props.pageData, pageFingerprint: "replacement", collectionId: "replacement"};
  assert.equal(model.state.candidates.length, 0);
  finish({ok: true, message: "旧记录的成功不能传给新记录"});
  await filling; await settle();
  assert.equal(results.length, 0);
  assert.equal(model.operation.fillMessage, "");
  assert.equal(model.operation.filling, false);
  assert.deepEqual(busy, [true, false]);
  assert.equal(calls.length, 2);
  assert.equal(model.state.selectedId, "");
});

test("brand evidence only offers the completed brand and model as one-click fill", async () => {
  const {default: Detail} = loadSource("features/review/ui/ReviewStepDetail.vue");
  const html = await renderToString(h(Detail, {
    step: {label: "品牌型号", category: "FIELD", page_target_field: "vehicle.brand_model",
      result_status: "CONFLICT", writable: true, evidence: [], values: [
        {source: "行驶证", value: "解放牌MODEL100A"}, {source: "铭牌", value: "MODEL100A80"},
        {source: "完整品牌型号", value: "解放牌MODEL100A80"}],
      details: {permitted_fill_values: ["解放牌MODEL100A80"]}},
    qrChecks: [], disabled: false, onFocusImage: async () => {}, onFill: async () => {}, onChoose: () => {},
  }));
  assert.equal((html.match(/aria-label="品牌型号回填材料值"/g) || []).length, 1);
  assert.match(html.replace(/<[^>]*>/g, ""), /解放牌MODEL100A80/);
});

test("two complete codes retain suffixes and reject incomplete or injected queries", () => {
  assert.deepEqual(validatedModelInput({announcementModel: " model100a80 ", engineModel: "锡柴 engine42-e6 ", pageScope: input.pageScope}), input);
  assert.equal(normalizeEngineCode("潍柴MC13H.51-61"), "MC13H.51-61");
  for (const code of ["", "MODEL%", "MODEL' OR 1=1", "MODEL_A"]) {
    assert.throws(() => validatedModelInput({...input, announcementModel: code}), /完整/);
  }
  assert.throws(() => validatedModelInput(null), /完整/);
});

test("model plan maps all visible spec fields and preserves absent cargo length", () => {
  const plan = modelFillPlan(candidate());
  assert.deepEqual(plan.values.slice(0, 3).map(item => [item.field, item.optionValue]),
    [["spec.brand", "2"], ["spec.series", "3"], ["vehicle.model", "10"]]);
  assert.equal(plan.values.length, 9);
  assert.equal(plan.values.find(item => item.field === "spec.engine_model").value, "ENGINE42-E6");
  assert.deepEqual(plan.missing, []);
  assert.match(plan.warnings.join(""), /不改写/);
  const preserved = modelFillPlan(candidate(), {"spec.cargo_length": "6.8"});
  assert.equal(preserved.values.at(-1).value, "6.8");
  assert.match(preserved.warnings.join(""), /保留页面原值/);
  for (const cargo of [null, "--", "0", "-1", "未知"]) {
    const item = candidate(); item.config.packingCaseLength = cargo;
    const empty = modelFillPlan(item);
    assert.ok(!empty.values.some(value => value.field === "spec.cargo_length"));
    assert.match(empty.warnings.join(""), /不改写/);
  }
  const complete = candidate(); complete.config.packingCaseLength = "6.8";
  assert.equal(modelFillPlan(complete).values.at(-1).value, "6.8");
  complete.config.fuelType = null;
  assert.deepEqual(modelFillPlan(complete).missing, ["燃料种类"]);
});

test("same-name records may be filled by ID while invalid IDs are rejected", () => {
  verifyModelOption(candidate());
  verifyModelOption({...candidate(), sameNameCount: 2});
  assert.throws(() => verifyModelOption({...candidate(), brandId: "undefined"}), /ID/);
});

test("serialized candidates compare by values, including nested config, never by dictionary order", () => {
  const original = candidate();
  const reordered = {...Object.fromEntries(Object.entries(original).reverse()),
    config: Object.fromEntries(Object.entries(original.config).reverse())};
  assert.equal(sameModelData(original, reordered), true);
  reordered.config.maxHp = "450";
  assert.equal(sameModelData(original, reordered), false);
  assert.equal(sameModelData(original, undefined), false);
  assert.equal(sameModelData([1, 2], [2, 1]), false);
});

test("same-name candidate cards show record ID and differences without pretending the name proves current selection", async t => {
  const first = {...candidate(), sameNameCount: 2, config: {...candidate().config, gearboxModel: "DT1420", rearAxleRatio: "3.42"}};
  const second = {...first, modelId: "11", config: {...first.config, gearboxModel: "DT1420超速挡", rearAxleRatio: "3.73"}};
  const {model, props} = modelHarness(t, async () => ({ok: true, searchId: "same-names", candidates: [first, second]}));
  await settle();
  props.pageData.pageFields["vehicle.model"] = first.modelName;
  model.select("11");
  const {default: Picker} = loadSource("features/review/ui/VehicleModelPicker.vue");
  const html = await renderToString(h(Picker, {disabled: false, model, pageData: props.pageData}));
  assert.match(html, /车型编号 10/);
  assert.match(html, /车型编号 11/);
  assert.match(html, /后桥速比 3.73/);
  assert.match(html, /名称与页面一致/);
  assert.doesNotMatch(html, /页面当前选择|无法可靠定位|原页面人工选择/);
  assert.equal(model.fillDisabledReason, "");
});

test("catalog requests use assistant background bridge and discard stale results", async () => {
  const previous = globalThis.chrome;
  let finish;
  let sent;
  globalThis.chrome = {runtime: {sendMessage: async message => {
    sent = message;
    return new Promise(resolve => { finish = resolve; });
  }}};
  try {
    let current = true;
    const result = searchVehicleModels(input, () => current, "https://test.forjtruck.com/consistency-qingdao");
    assert.deepEqual(sent, {type: "QUERY_VEHICLE_MODEL_CATALOG", input, expectedPageUrl: "https://test.forjtruck.com/consistency-qingdao"});
    current = false;
    finish({ok: true, candidates: [candidate()]});
    await assert.rejects(result, /页面已变化/);
    assert.equal(sent.type, "QUERY_VEHICLE_MODEL_CATALOG");
  } finally { globalThis.chrome = previous; }
});

test("background only calls assistant-owned query endpoint and surfaces unavailable configuration", async () => {
  const result = await requestModelCatalog(input, async (url, init) => {
    assert.match(url, /\/api\/review\/vehicle-models\/search$/);
    assert.ok(!url.includes("forjtruck.com"));
    assert.deepEqual(JSON.parse(init.body), input);
    assert.equal(init.headers.Authorization, undefined);
    return {ok: true, json: async () => [candidate()]};
  });
  assert.equal(result.candidates[0].modelId, "10");
  const failure = await requestModelCatalog(input, async () => ({ok: false, json: async () => ({detail: "正式车型库尚未配置"})}));
  assert.deepEqual(failure, {ok: false, message: "正式车型库尚未配置"});
});

test("search and fill carry the original tab, record, step and collection identity", async () => {
  const previous = globalThis.chrome;
  const messages = [];
  globalThis.chrome = {
    runtime: {sendMessage: async message => { messages.push({tab: message.sourceTabId, ...message}); return {ok: true}; }},
    tabs: {sendMessage: async (tab, message) => { messages.push({tab, ...message}); return {ok: true}; }},
  };
  const page = {sourceTabId: 12, pageUrl: "https://example.test/1", pageInstanceId: "instance",
    pageFingerprint: "record", collectionId: "collection"};
  try {
    await queryVehicleModels(page, input);
    await fillVehicleModel(page, "search1", "10");
    assert.equal(messages[0].type, "SEARCH_REVIEW_VEHICLE_MODELS");
    assert.equal(messages[1].type, "APPLY_VEHICLE_MODEL");
    for (const message of messages) {
      assert.equal(message.tab, 12);
      assert.equal(message.expectedPageFingerprint, "record");
      assert.equal(message.expectedPageInstanceId, "instance");
      assert.equal(message.expectedCollectionId, "collection");
    }
    assert.equal(messages[1].modelId, "10");
    assert.equal(messages[1].searchId, "search1");
    assert.equal(messages[1].candidate, undefined); // 页面只接收缓存候选ID，不能夹带自定义配置。
  } finally { globalThis.chrome = previous; }
});

const control = (value = "") => ({tagName: "INPUT", value, isConnected: true,
  getAttribute: () => null, dispatchEvent() {}});
const root = () => ({defaultView: {Event: class Event {} }});

test("dependent fill resolves children again after parent rerenders instead of writing detached controls", async () => {
  const controls = {brand: control("旧品牌"), series: control("旧车系"), hp: control("300")};
  const oldSeries = controls.series;
  controls.brand.dispatchEvent = () => {
    oldSeries.isConnected = false;
    controls.series = control("");
  };
  const values = [{field: "brand", label: "品牌", value: "新品牌"},
    {field: "series", label: "车系", value: "新车系"}, {field: "hp", label: "马力", value: "420"}];
  const result = await ReviewPageFieldWriter.executeSequence(root(), values, field => controls[field],
    () => true, {fields: new Set(values.map(item => item.field))});
  assert.equal(result.ok, true);
  assert.equal(controls.series.value, "新车系");
  assert.equal(oldSeries.value, "旧车系");
});

test("partial failure and changed record stop dependent writes without claiming complete success", async () => {
  const first = control("old"), second = control("old2");
  const values = [{field: "first", label: "第一项", value: "new"}, {field: "second", label: "第二项", value: "new2"}];
  const policy = {fields: new Set(["first", "second"])};
  const failed = await ReviewPageFieldWriter.executeSequence(root(), values,
    field => field === "first" ? first : null, () => true, policy);
  assert.equal(failed.ok, false);
  assert.match(failed.message, /未完整回填/);
  assert.equal(failed.actions[0].field, "first");
  let current = true;
  first.value = "old";
  first.dispatchEvent = () => { current = false; };
  const stale = await ReviewPageFieldWriter.executeSequence(root(), values,
    field => field === "first" ? first : second, () => current, policy);
  assert.equal(stale.ok, false);
  assert.equal(second.value, "old2");
});

test("late page overwrite fails final readback instead of reporting completed specs", async () => {
  const first = control(), second = control();
  second.dispatchEvent = () => { first.value = "联动改写"; };
  const values = [{field: "first", label: "第一项", value: "A"}, {field: "second", label: "第二项", value: "B"}];
  const result = await ReviewPageFieldWriter.executeSequence(root(), values,
    field => field === "first" ? first : second, () => true, {fields: new Set(["first", "second"])});
  assert.equal(result.ok, false);
  assert.match(result.message, /最终回读不一致/);
});

test("pagination retains selection across pages without querying and resets on new search or edit", async t => {
  const candidates = Array.from({length: 12}, (_, i) => ({...candidate(), modelId: String(i + 1), modelName: `完整车型名称 ${i + 1}`}));
  const {model, props, calls} = modelHarness(t, async () => ({ok: true, candidates, searchId: "all-12"}));
  await settle();
  const {default: Picker} = loadSource("features/review/ui/VehicleModelPicker.vue");
  assert.equal(model.pageCount, 3);
  assert.equal(model.pageCandidates.length, 5);
  model.select("3");
  model.setPage(3);
  assert.equal(model.state.selectedId, "3");
  assert.equal(model.selectedPage, 1);
  assert.equal(model.pageCandidates.length, 2);
  assert.equal(model.range, "11–12");
  assert.equal(calls.length, 1);
  const html = await renderToString(h(Picker, {disabled: false, model, pageData: props.pageData}));
  assert.equal((html.match(/type="radio"/g) || []).length, 2);
  assert.match(html, /回到所选页 · 第 1 页/);
  assert.match(html, /完整车型名称 3/);
  assert.match(html, /当前 11–12 条/);
  assert.match(html, /aria-current="page"/);
  props.disabled = true; model.setPage(1);
  assert.equal(model.state.page, 3);
  props.disabled = false; model.setPage(model.selectedPage);
  assert.equal(model.pageCandidates[2].modelId, "3");
  model.setPage(0); model.setPage(4); model.setPage(1.5);
  assert.equal(model.state.page, 1);
  model.setPage(2);
  await model.search();
  assert.equal(model.state.page, 1);
  assert.equal(model.state.selectedId, "");
  model.setPage(2); model.select("6");
  model.edit("engineModel", "ENGINE50-E6");
  assert.equal(model.state.page, 1);
  assert.equal(model.state.selectedId, "");
  assert.equal(model.pageCandidates.length, 0);
});

test("compact model evidence preserves readings and uncertainty with one original image per material", async t => {
  const {model, props} = modelHarness(t);
  await settle();
  const {default: Picker} = loadSource("features/review/ui/VehicleModelPicker.vue");
  const step = {evidence: [
    {source: "铭牌", field: "vehicle.announcement_model", value: input.announcementModel, image_id: "plate"},
    {source: "铭牌", field: "vehicle.engine_model", value: input.engineModel, image_id: "plate", uncertain: true},
  ]};
  const html = await renderToString(h(Picker, {disabled: false, model, step, onFocusImage: async () => {},
    pageData: {...props.pageData, images: [{imageId: "plate", src: "https://example.test/plate.jpg"}]}}));
  assert.equal((html.match(/<img/g) || []).length, 1);
  assert.equal((html.match(/查看原图/g) || []).length, 1);
  assert.match(html, /识别不确定，请核对原图/);
  assert.match(html, /整车型号/);
  assert.match(html, /发动机型号/);
  assert.match(html, /材料依据/);
});

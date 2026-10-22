import sourceManifest from "./fixtures/collect-manifest-vehicle-source.json";
import { onTestFinished } from "vitest";
import assert from "node:assert/strict";
import { test } from "vitest";
import {createVehicleModelFiller} from "../src/platform/browser/actions/vehicle-model-fill.ts";
import {ReviewPageFieldWriter} from "../src/platform/browser/actions/page-field-writer.ts";
import {modelFillPlan} from "../src/shared/vehicle-model.ts";

const candidate = {modelId: "39503", modelName: "东风商用车 天龙重卡 启航版 450马力 6X4牵引车(DFH4250A4)(国五)",
  brandId: "6", brandName: "东风商用车", seriesId: "23", seriesName: "东风天龙", typeLevel: "牵引车", sameNameCount: 2,
  warnings: [], config: {driveForm: "6X4", fuelType: "柴油", emissionStandard: "国五", maxHp: "450", engineBrand: "东风雷诺", engineType: "东风雷诺dCi450-51"}};
const mappings = sourceManifest.actions.find(a => a.handler_id === "vehicle_model_fill").mappings;
const input = {announcementModel: "DFH4250A4", engineModel: "DCI450-51", pageScope: {brandId: "6", brandName: "东风商用车", seriesId: "23", seriesName: "东风天龙", typeLevel: "牵引车"}};
const request = {modelId: candidate.modelId, searchId: "trusted-search", expectedPageUrl: "http://test.forjtruck.com/consistency-qingdao?showPageModel=1"};

function fixture(t, changes = {}) {
  const globals = globalThis.chrome, execute = ReviewPageFieldWriter.executeSequence;
  onTestFinished(() => {globalThis.chrome = globals; ReviewPageFieldWriter.executeSequence = execute;});
  const fields = new Map(), nodes = new Map(), queries = [];
  const selection = {brand: {id: "6", label: "东风商用车"}, series: {id: "23", label: "东风天龙"},
    model: {id: "34922", label: "当前420马力车型"}, vehicleType: "牵引车"};
  const values = [...modelFillPlan(candidate, {}, mappings, {}, selection).values,
    {field: "spec.brand", value: "东风商用车", optionValue: "6"},
    {field: "spec.series", value: "东风天龙", optionValue: "23"}];
  for (const item of values) {
    const node = {tagName: item.optionValue ? "SELECT" : "INPUT", value: item.optionValue || item.value,
      isConnected: true, getAttribute: () => null};
    if (item.optionValue) {
      node.options = [{value: item.optionValue, textContent: item.value}];
      if (item.field === "vehicle.model") {
        node.options.unshift({value: "34922", textContent: "当前420马力车型"}); node.value = "34922";
      }
    }
    if (item.field === "spec.max_hp") node.value = "420";
    nodes.set(item.field, node);
    const snap = ReviewPageFieldWriter.captureValue(node);
    fields.set(item.field, {element: node, collectionId: "collection-1", collectedValue: snap.value, controlSnapshot: snap});
  }
  nodes.set("vehicle.type", {value: "牵引车"});
  const current = {value: true};
  const originalSelection = {brand: {id: "6", label: "东风商用车"}, series: {id: "23", label: "东风天龙"},
    model: {id: "34922", label: "当前420马力车型"}, vehicleType: "牵引车"};
  const context = {selectionSnapshot: () => originalSelection, root: {}, fields, isCurrent: () => current.value, collectionId: () => "collection-1",
    collect: keys => ({pageFields: Object.fromEntries(keys.map(key => [key,
      key === "vehicle.type" ? nodes.get(key).value : ReviewPageFieldWriter.captureValue(nodes.get(key))?.value || ""])),
      fieldTargets: keys.filter(key => nodes.has(key)).map(field => ({field, element: nodes.get(field)}))}),
    mappings: () => mappings, writableFields: () => new Set(values.map(item => item.field)), controlKinds: () => new Set(["text", "select"]),
    resolveSearch: async () => ({ok: true, input, candidate}), ...changes};
  globalThis.chrome = {runtime: {sendMessage: async message => {queries.push(message); return {ok: true, candidates: [candidate]};}}};
  return {originalSelection, filler: createVehicleModelFiller(context), context, nodes, fields, queries, current};
}

test("trusted ID selection completes all specification fields and updates their live snapshots", async t => {
  const f = fixture(t);
  const result = await f.filler.fill({...request, candidate: {...candidate, modelName: "伪造名称不得写入"}});
  assert.equal(result.ok, true, result.message);
  assert.equal(result.actions.length, 7);
  assert.ok(result.actions.every(item => item.field !== "spec.brand"));
  assert.equal(f.nodes.get("vehicle.model").value, "39503");
  assert.equal(f.nodes.get("spec.max_hp").value, "450");
  assert.equal(f.fields.get("vehicle.model").collectedValue, candidate.modelName);
  assert.equal(f.queries[0].type, "QUERY_VEHICLE_MODEL_CATALOG");
  assert.deepEqual(f.queries[0].input, input);
});

test("scope or control changes stop before writing and have separate actionable failure codes", async t => {
  const f = fixture(t);
  f.nodes.get("vehicle.type").value = "载货车";
  const scope = await f.filler.fill(request);
  assert.equal(scope.code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.equal(f.queries.length, 0);
  f.nodes.get("vehicle.type").value = "牵引车";
  f.nodes.get("spec.max_hp").value = "500";
  assert.equal((await f.filler.fill(request)).code, "MODEL_FIELD_CHANGED");
  assert.equal(f.nodes.get("vehicle.model").value, "34922");
});

test("a pending request cannot survive a new collection or concurrent fill", async t => {
  let release;
  const f = fixture(t, {resolveSearch: () => new Promise(resolve => {release = resolve;})});
  const pending = f.filler.fill(request);
  assert.equal((await f.filler.fill(request)).code, "WRITE_IN_PROGRESS");
  f.filler.invalidate();
  release({ok: true, input, candidate});
  assert.equal((await pending).code, "PAGE_IDENTITY_CHANGED");
  assert.equal(f.queries.length, 0);
  assert.equal(f.nodes.get("vehicle.model").value, "34922");
});

test("a write interruption keeps actual partial values but never marks them fully filled", async t => {
  const f = fixture(t);
  ReviewPageFieldWriter.executeSequence = async () => {
    f.nodes.get("spec.max_hp").value = "450";
    throw new Error("下拉连接中断");
  };
  const result = await f.filler.fill(request);
  assert.equal(result.ok, false);
  assert.equal(result.code, "MODEL_WRITE_INTERRUPTED");
  assert.match(result.message, /请核对已写入字段/);
  assert.equal(f.fields.get("spec.max_hp").collectedValue, "450");
  assert.ok(result.actions.every(action => action.status === "UNCONFIRMED"));
});

test("a confirmed model change survives a later spec failure and can retry without a stale selection error", async t => {
  const f = fixture(t), execute = ReviewPageFieldWriter.executeSequence;
  ReviewPageFieldWriter.executeSequence = async () => {
    f.nodes.get("vehicle.model").value = candidate.modelId;
    return {ok: false, message: "驱动形式最终回读不一致", actions: [
      {field: "spec.series", value: candidate.seriesName, status: "UNCHANGED"},
      {field: "vehicle.model", value: candidate.modelName, status: "FILLED"},
    ]};
  };
  const partial = await f.filler.fill(request);
  assert.equal(partial.ok, false);
  assert.ok(partial.actions.every(item => item.status === "UNCONFIRMED"));
  ReviewPageFieldWriter.executeSequence = execute;
  const retried = await f.filler.fill(request);
  assert.equal(retried.ok, true, retried.message);
  const node = f.nodes.get("vehicle.model");
  node.options.push({value: "999", textContent: candidate.modelName}); node.value = "999";
  assert.equal((await f.filler.fill(request)).code, "MODEL_PAGE_SCOPE_CHANGED");
});

test("switching records during the write cannot return success or update the replacement record", async t => {
  const f = fixture(t);
  ReviewPageFieldWriter.executeSequence = async () => {f.current.value = false; return {ok: true, message: "不会对外报告成功"};};
  const result = await f.filler.fill(request);
  assert.equal(result.ok, false);
  assert.equal(result.code, "PAGE_IDENTITY_CHANGED");
  assert.equal(f.fields.get("spec.max_hp").collectedValue, "420");
});


test("candidate scope cannot change the original page brand or series", async t => {
  const f = fixture(t);
  for (const [field, original, alias] of [["spec.brand", f.originalSelection.brand, "页面品牌别名"], ["spec.series", f.originalSelection.series, "页面车系别名"]]) {
    const node = f.nodes.get(field);
    node.options[0].textContent = alias;
    const originalId = field === "spec.brand" ? "7" : "24";
    node.options.push({value: originalId, textContent: "原页面名称"});
    node.value = originalId; original.id = originalId; original.label = "原页面名称";
    const snapshot = ReviewPageFieldWriter.captureValue(node);
    f.fields.set(field, {element: node, collectionId: "collection-1", collectedValue: snapshot.value, controlSnapshot: snapshot});
  }
  const result = await f.filler.fill(request);
  assert.equal(result.ok, false);
  assert.equal(result.code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.equal(f.nodes.get("spec.brand").value, "7");
  assert.equal(f.nodes.get("spec.series").value, "24");
  assert.equal(f.queries.length, 0);
});

test("same-label different page ID blocks fill before catalog query", async t => {
  const f = fixture(t), node = f.nodes.get("spec.series");
  node.options.push({value: "24", textContent: "东风天龙"}); node.value = "24";
  const result = await f.filler.fill(request);
  assert.equal(result.code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.equal(f.queries.length, 0);
});

test("a broad catalog result cannot fill an ID absent from the real business options", async t => {
  const f = fixture(t);
  f.nodes.get("vehicle.model").options = [{value: "34922", textContent: "当前420马力车型"}];
  const result = await f.filler.fill(request);
  assert.equal(result.ok, false);
  assert.match(result.message, /不存在唯一可用 ID/);
  assert.equal(f.nodes.get("vehicle.model").value, "34922");
});


test("verified writes update the ID baseline while later same-label manual changes still block", async t => {
  const f = fixture(t);
  assert.equal((await f.filler.fill(request)).ok, true);
  assert.equal((await f.filler.fill(request)).ok, true);
  const node = f.nodes.get("vehicle.model");
  node.options.push({value: "999", textContent: candidate.modelName}); node.value = "999";
  assert.equal((await f.filler.fill(request)).code, "MODEL_PAGE_SCOPE_CHANGED");
});

test("a vehicle type change during writing is reported as partial work", async t => {
  const f = fixture(t);
  ReviewPageFieldWriter.executeSequence = async () => {
    f.nodes.get("vehicle.model").value = candidate.modelId;
    f.nodes.get("vehicle.type").value = "载货车";
    return {ok: true, message: "写入结束"};
  };
  const result = await f.filler.fill(request);
  assert.equal(result.ok, false);
  assert.equal(result.code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.ok(result.actions.every(item => item.status === "UNCONFIRMED"));
});

test("brand and series are excluded while model precedes all six specs", async t => {
  const f = fixture(t);
  const execute = ReviewPageFieldWriter.executeSequence;
  const writes = [];
  ReviewPageFieldWriter.executeSequence = async (...args) => {
    writes.push(...args[1].map(item => item.field));
    assert.ok(!writes.includes("spec.brand"));
    assert.ok(!writes.includes("spec.series"));
    assert.equal(writes[0], "vehicle.model");
    return execute(...args);
  };
  const result = await f.filler.fill(request);
  assert.equal(result.ok, true, result.message);
  assert.equal(f.nodes.get("spec.brand").value, "6");
  assert.equal(f.nodes.get("spec.series").value, "23");
  assert.equal(f.nodes.get("vehicle.model").value, candidate.modelId);
});

test("out-of-scope candidates and queries cannot reach the writer", async t => {
  for (const change of [{typeLevel: "载货车"}, {brandId: "7"}, {seriesId: "24"}]) {
    const f = fixture(t, {resolveSearch: async () => ({ok: true, candidate, input: {...input, pageScope: {...input.pageScope, ...change}}})});
    const result = await f.filler.fill(request);
    assert.equal(result.ok, false);
    assert.equal(result.code, "MODEL_PAGE_SCOPE_CHANGED");
    assert.equal(f.queries.length, 0);
    assert.equal(f.nodes.get("vehicle.model").value, "34922");
  }
});


test("failed final option inspection reports actual partial writes without claiming completion", async t => {
  const f = fixture(t);
  ReviewPageFieldWriter.executeSequence = async () => {
    const node = f.nodes.get("vehicle.model"); node.value = candidate.modelId;
    node.options.push({...node.options.find(item => item.value === candidate.modelId)});
    return {ok: true, message: "写入结束"};
  };
  const result = await f.filler.fill(request);
  assert.equal(result.ok, false);
  assert.equal(result.code, "MODEL_READBACK_FAILED");
  assert.match(result.message, /核对已写入字段/);
  assert.equal(result.actions.find(item => item.field === "vehicle.model").value, candidate.modelName);
  assert.ok(result.actions.every(item => item.status === "UNCONFIRMED"));
});

test.each(["disabled", "duplicate"])("native %s model IDs stop before specification writes", async kind => {
  const f = fixture(null);
  const model = f.nodes.get("vehicle.model");
  const target = model.options.find(item => item.value === candidate.modelId);
  if (kind === "disabled") target.disabled = true;
  else model.options.push({...target});
  const result = await f.filler.fill(request);
  assert.equal(result.ok, false);
  assert.equal(result.code, kind === "disabled" ? "SELECT_OPTION_DISABLED" : "SELECT_OPTION_DUPLICATE");
  assert.equal(f.nodes.get("spec.max_hp").value, "420");
  assert.equal(model.value, "34922");
});

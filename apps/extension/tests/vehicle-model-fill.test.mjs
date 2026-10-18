import assert from "node:assert/strict";
import test from "node:test";
import {createVehicleModelFiller} from "../src/platform/browser/actions/vehicle-model-fill.ts";
import {ReviewPageFieldWriter} from "../src/platform/browser/actions/page-field-writer.ts";
import {modelFillPlan} from "../src/shared/vehicle-model.ts";

const candidate = {modelId: "39503", modelName: "东风商用车 天龙重卡 启航版 450马力 6X4牵引车(DFH4250A4)(国五)",
  brandId: "6", brandName: "东风商用车", seriesId: "23", seriesName: "东风天龙", typeLevel: "牵引车", sameNameCount: 2,
  warnings: [], config: {driveForm: "6X4", fuelType: "柴油", emissionStandard: "国五", maxHp: "450", engineBrand: "东风雷诺", engineType: "东风雷诺dCi450-51"}};
const input = {announcementModel: "DFH4250A4", engineModel: "DCI450-51", pageScope: {
  brandName: "东风商用车", seriesName: "东风天龙", typeLevel: "牵引车"}};
const request = {modelId: candidate.modelId, searchId: "trusted-search", expectedPageUrl: "http://test.forjtruck.com/consistency-qingdao?showPageModel=1"};

function fixture(t, changes = {}) {
  const globals = globalThis.chrome, execute = ReviewPageFieldWriter.executeSequence;
  t.after(() => {globalThis.chrome = globals; ReviewPageFieldWriter.executeSequence = execute;});
  const fields = new Map(), nodes = new Map(), queries = [];
  const values = modelFillPlan(candidate).values;
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
  const context = {root: {}, fields, isCurrent: () => current.value, collectionId: () => "collection-1",
    collect: keys => ({pageFields: Object.fromEntries(keys.map(key => [key,
      key === "vehicle.type" ? nodes.get(key).value : ReviewPageFieldWriter.captureValue(nodes.get(key))?.value || ""])),
      fieldTargets: keys.filter(key => nodes.has(key)).map(field => ({field, element: nodes.get(field)}))}),
    writableFields: () => new Set(values.map(item => item.field)), controlKinds: () => new Set(["text", "select"]),
    resolveSearch: async () => ({ok: true, input, candidate}), ...changes};
  globalThis.chrome = {runtime: {sendMessage: async message => {queries.push(message); return {ok: true, candidates: [candidate]};}}};
  return {filler: createVehicleModelFiller(context), context, nodes, fields, queries, current};
}

test("trusted ID selection completes all specification fields and updates their live snapshots", async t => {
  const f = fixture(t);
  const result = await f.filler.fill({...request, candidate: {...candidate, modelName: "伪造名称不得写入"}});
  assert.equal(result.ok, true, result.message);
  assert.equal(result.actions.length, 9);
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

test("switching records during the write cannot return success or update the replacement record", async t => {
  const f = fixture(t);
  ReviewPageFieldWriter.executeSequence = async () => {f.current.value = false; return {ok: true, message: "不会对外报告成功"};};
  const result = await f.filler.fill(request);
  assert.equal(result.ok, false);
  assert.equal(result.code, "PAGE_IDENTITY_CHANGED");
  assert.equal(f.fields.get("spec.max_hp").collectedValue, "420");
});

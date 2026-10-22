import assert from "node:assert/strict";
import { test } from "vitest";
import { createModelSessionHandler } from "../src/platform/extension/vehicle-model-sessions.ts";
import { createModelPageVerifier } from "../src/platform/extension/vehicle-model-page.ts";

const input = {announcementModel: "MODEL100A80", engineModel: "ENGINE42-E6",
  pageScope: {brandId: "2", brandName: "示例品牌", seriesId: "3", seriesName: "示例车系", typeLevel: "牵引车"}};
const context = {sourceTabId: 12, expectedPageUrl: "https://admin.forjtruck.com/consistency-changchun?showPageModel=1",
  expectedPageInstanceId: "instance", expectedPageFingerprint: "record", expectedCollectionId: "collection"};
const panel = {id: "extension", url: "chrome-extension://extension/index.html"};
const content = {id: "extension", tab: {id: 12}, frameId: 0, url: context.expectedPageUrl};
const candidate = {modelId: "10", modelName: "完整目录车型名称", config: {engineType: "ENGINE42-E6"}};
const search = (extra = {}) => ({type: "SEARCH_REVIEW_VEHICLE_MODELS", input, ...context, ...extra});
const resolve = (id, extra = {}) => ({type: "RESOLVE_REVIEW_MODEL_SEARCH", ...context, searchId: id, modelId: "10", ...extra});
function harness(query = async () => ({ok: true, candidates: [candidate]})) {
  const records = {};
  const store = {get: async key => structuredClone({[key]: records[key]}),
    set: async values => { Object.assign(records, structuredClone(values)); }};
  let n = 0;
  const tabs = new Map([[12, {url: context.expectedPageUrl}]]);
  const verifyPage = createModelPageVerifier("extension", async id => {
    if (!tabs.has(id)) throw new Error("No tab with id");
    return tabs.get(id);
  });
  const live = {ok: true, pageScope: structuredClone(input.pageScope)};
  const scopeReads = [];
  const create = () => createModelSessionHandler("extension", store, query, verifyPage, async context => {
    scopeReads.push(context); return structuredClone(live);
  }, () => `session-${++n}`);
  return {records, store, tabs, live, scopeReads, handle: create(), create};
}

test("sidepanel searches review service directly and trusted session survives worker restart for content fill", async () => {
  const seen = [];
  const h = harness(async value => { seen.push(value); return {ok: true, candidates: [candidate]}; });
  const result = await h.handle(search(), panel);
  assert.equal(result.ok, true);
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0], input);
  // 新 worker 实例没有内存缓存，从扩展 session storage 恢复当前会话。
  const restored = h.create();
  const found = await restored(resolve(result.searchId), content);
  assert.equal(found.ok, true);
  assert.deepEqual(found.candidate, candidate);
  assert.deepEqual(found.input, input);
  assert.equal(seen.length, 1);
  const forged = await restored(resolve(result.searchId, {modelId: "999", candidate: {modelId: "999"}}), content);
  assert.equal(forged.ok, false);
  assert.match(forged.message, /不属于当前查询/);
});

test("storage may reorder every dictionary key without expiring a just-searched session after restart", async () => {
  const sorted = value => Array.isArray(value) ? value.map(sorted) : value && typeof value === "object"
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
  const h = harness();
  h.store.set = async values => Object.assign(h.records, sorted(values));
  const result = await h.handle(search(), panel);
  assert.equal(result.ok, true);
  assert.notEqual(JSON.stringify(h.records["reviewVehicleModel:12"].context), JSON.stringify(context));
  const found = await h.create()(resolve(result.searchId), content);
  assert.equal(found.ok, true);
  assert.deepEqual(found.candidate, candidate);
  assert.deepEqual(found.input, input);
  const changed = await h.create()(resolve(result.searchId, {expectedCollectionId: "new"}), content);
  assert.equal(changed.code, "PAGE_IDENTITY_CHANGED");
  const replaced = await h.create()(resolve("other"), content);
  assert.equal(replaced.code, "MODEL_SEARCH_CHANGED");
});

test("unknown senders, business pages posing as panel, unsupported paths and incomplete identity cannot query", async () => {
  let queries = 0;
  const h = harness(async () => { queries++; return {ok: true, candidates: []}; });
  for (const sender of [content, {...panel, id: "foreign"}, {...panel, url: "chrome-extension://extension/other.html"},
    {...panel, url: "https://extension/index.html"}, {...panel, tab: {id: 12}}]) {
    assert.equal((await h.handle(search(), sender)).ok, false);
  }
  for (const extra of [{sourceTabId: -1}, {sourceTabId: 1.5}, {expectedPageInstanceId: ""}, {expectedPageFingerprint: ""},
    {expectedCollectionId: ""}, {expectedPageUrl: "https://evil.test/consistency-changchun"},
    {expectedPageUrl: "https://admin.forjtruck.com/scrap-replacement-changchun"}, {input: {...input, engineModel: "' OR 1=1"}}]) {
    assert.equal((await h.handle(search(extra), panel)).ok, false);
  }
  assert.equal(queries, 0);
  assert.deepEqual(h.records, {});
});

test("session resolve rejects different tab, URL, record, instance and collection even with a valid model ID", async () => {
  const h = harness();
  const result = await h.handle(search(), panel);
  for (const extra of [{expectedPageFingerprint: "other"}, {expectedPageInstanceId: "other"},
    {expectedCollectionId: "other"}, {expectedPageUrl: context.expectedPageUrl + "&record=2"}, {searchId: "other"}]) {
    assert.equal((await h.handle(resolve(result.searchId, extra), content)).ok, false);
  }
  for (const sender of [panel, {...content, id: "foreign"}, {...content, tab: {id: 13}},
    {...content, url: "https://evil.test/consistency-changchun"}, {...content, frameId: 1}]) {
    assert.equal((await h.handle(resolve(result.searchId), sender)).ok, false);
  }
  h.tabs.set(12, {url: context.expectedPageUrl + "&record=2"});
  assert.equal((await h.handle(resolve(result.searchId), content)).code, "PAGE_IDENTITY_CHANGED");
});

test("SPA content sender may retain the login URL while the live tab has entered vehicle review", async () => {
  const h = harness();
  const result = await h.handle(search(), panel);
  for (const path of ["/", "/login", "/home", "/scrap-replace-changchun", "/consistency-changchun"]) {
    const sender = {...content, url: `https://admin.forjtruck.com${path}`, origin: "https://admin.forjtruck.com"};
    const found = await h.create()(resolve(result.searchId), sender);
    assert.equal(found.ok, true, `${path}: ${found.message}`);
    assert.equal(found.candidate.modelId, "10");
  }
  h.tabs.set(12, {url: "https://admin.forjtruck.com/home"});
  const gone = await h.handle(resolve(result.searchId), content);
  assert.equal(gone.ok, false);
  assert.equal(gone.code, "PAGE_IDENTITY_CHANGED");
});

test("new search invalidates previous candidates immediately and late responses cannot overwrite it", async () => {
  const pending = [];
  const h = harness(value => new Promise(done => pending.push({value, done})));
  const first = h.handle(search(), panel);
  await new Promise(setImmediate);
  assert.equal(pending.length, 1);
  pending[0].done({ok: true, candidates: [candidate]});
  const initial = await first;
  const older = h.handle(search({input: {...input, engineModel: "ENGINE50-E6"}}), panel);
  await new Promise(setImmediate);
  assert.equal((await h.handle(resolve(initial.searchId), content)).ok, false);
  const latest = h.handle(search({input: {...input, engineModel: "ENGINE60-E6"}}), panel);
  await new Promise(setImmediate);
  pending[2].done({ok: true, candidates: [{...candidate, modelName: "最新完整名称"}]});
  const current = await latest;
  pending[1].done({ok: true, candidates: [candidate]});
  assert.equal((await older).ok, false);
  const found = await h.handle(resolve(current.searchId), content);
  assert.equal(found.candidate.modelName, "最新完整名称");
  assert.equal(found.input.engineModel, "ENGINE60-E6");
  assert.equal(Object.keys(h.records).length, 1);
});

test("service and storage failures remain errors, never successful empty lists or cached old options", async () => {
  let response = {ok: true, candidates: [candidate]};
  const h = harness(async () => response);
  const first = await h.handle(search(), panel);
  response = {ok: false, message: "正式车型库尚未配置"};
  const failed = await h.handle(search(), panel);
  assert.equal(failed.ok, false);
  assert.equal(failed.message, "正式车型库尚未配置");
  assert.equal((await h.handle(resolve(first.searchId), content)).ok, false);
  response = {ok: true, candidates: undefined};
  assert.match((await h.handle(search(), panel)).message, /响应不完整/);
  const network = harness(async () => { throw new Error("Failed to fetch"); });
  assert.match((await network.handle(search(), panel)).message, /审核服务连接失败/);
  h.store.set = async () => { throw new Error("QUOTA_BYTES"); };
  assert.match((await h.handle(search(), panel)).message, /会话保存或读取失败/);
});


test("one query retains every candidate with the same verified fixed scope", async () => {
  const rows = Array.from({length: 101}, (_, i) => ({...candidate, modelId: String(i + 1)}));
  const h = harness(async () => ({ok: true, candidates: rows}));
  const result = await h.handle(search(), panel);
  assert.equal(result.candidates.length, 101);
  const found = await h.create()(resolve(result.searchId, {modelId: "101"}), content);
  assert.equal(found.ok, true);
  assert.deepEqual(found.input, input);
});


test("panel cannot override the fixed page brand or vehicle type", async () => {
  const calls = [];
  const h = harness(async query => {calls.push(query); return {ok: true, candidates: []};});
  for (const change of [{brandId: "9"}, {brandId: ""}, {brandName: "伪造品牌"}, {typeLevel: "载货车"}, {typeLevel: ""}]) {
    const result = await h.handle(search({input: {...input, pageScope: {...input.pageScope, ...change}}}), panel);
    assert.equal(result.ok, false);
  }
  assert.deepEqual(calls, []);
  assert.equal((await h.handle({type: "LOAD_REVIEW_MODEL_CHOICES", ...context}, panel)).ok, false);
});

test("page scope is verified before and after the request and changed-page results cannot be resolved", async () => {
  const h = harness(async () => {
    h.live.pageScope.brandId = "9";
    return {ok: true, candidates: [candidate]};
  });
  const result = await h.handle(search(), panel);
  assert.equal(result.ok, false);
  assert.equal(result.code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.equal(h.scopeReads.length, 2);
  assert.deepEqual(h.scopeReads[0], context);
  assert.equal(h.records["reviewVehicleModel:12"].candidates, null);
  assert.equal((await h.handle(resolve(h.records["reviewVehicleModel:12"].id), content)).ok, false);
});

test("live page identity failure prevents any catalog request", async () => {
  let count = 0;
  const h = harness(async () => {count++; return {ok: true, candidates: []};});
  Object.assign(h.live, {ok: false, code: "PAGE_IDENTITY_CHANGED", message: "页面已变化，请重新采集"});
  const result = await h.handle(search(), panel);
  assert.equal(result.code, "PAGE_IDENTITY_CHANGED");
  assert.equal(count, 0);
});

test("changing the basic vehicle type during a catalog request discards its candidates", async () => {
  const h = harness(async () => {
    h.live.pageScope.typeLevel = "载货车";
    return {ok: true, candidates: [candidate]};
  });
  const result = await h.handle(search(), panel);
  assert.equal(result.ok, false);
  assert.equal(result.code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.equal(h.records["reviewVehicleModel:12"].candidates, null);
});

test("panel cannot override or clear the fixed page series", async () => {
  let calls = 0;
  const h = harness(async () => {calls++; return {ok: true, candidates: [candidate]};});
  for (const seriesId of ["4", ""]) {
    const result = await h.handle(search({input: {...input, pageScope: {...input.pageScope, seriesId}}}), panel);
    assert.equal(result.ok, false);
  }
  assert.equal(calls, 0);
});


test("changing the series during a query discards the result", async () => {
  let h;
  h = harness(async () => {
    h.live.pageScope.seriesId = "4";
    return {ok: true, candidates: [candidate]};
  });
  const result = await h.handle(search(), panel);
  assert.equal(result.code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.equal(h.records["reviewVehicleModel:12"].candidates, null);
});


test("a new query replaces configuration and invalidates the previous preview", async () => {
  let rows = [candidate];
  const h = harness(async () => ({ok: true, candidates: rows}));
  const first = await h.handle(search(), panel);
  rows = [{...candidate, config: {engineType: "CHANGED42"}}];
  const second = await h.handle(search(), panel);
  assert.notEqual(first.searchId, second.searchId);
  assert.equal((await h.handle(resolve(first.searchId), content)).code, "MODEL_SEARCH_CHANGED");
  assert.equal((await h.handle(resolve(second.searchId), content)).candidate.config.engineType, "CHANGED42");
});

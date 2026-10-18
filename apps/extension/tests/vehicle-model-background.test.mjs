import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const pageUrl = "http://test.forjtruck.com/consistency-qingdao?showPageModel=1";
const panel = {id: "extension", url: "chrome-extension://extension/index.html"};
// 浏览器消息中的旧入口地址与 tabs.get 读到的当前路由不同。
const content = {id: "extension", tab: {id: 12, url: "http://test.forjtruck.com/"},
  url: "http://test.forjtruck.com/login", origin: "http://test.forjtruck.com", frameId: 0, documentId: "doc-1"};
const input = {announcementModel: "DFH4250A4", engineModel: "DCI450-51",
  pageScope: {brandName: "东风商用车", seriesName: "东风天龙", typeLevel: "牵引车"}};
const identity = {expectedPageUrl: pageUrl, expectedPageInstanceId: "instance-1",
  expectedPageFingerprint: "record-1", expectedCollectionId: "collection-1"};

function background({inspectionError} = {}) {
  const records = {}, listeners = [], queries = [], injections = [];
  const tabs = new Map([[12, {url: pageUrl}]]);
  const candidate = {modelId: "45759", modelName: "完整车型名称", config: {maxHp: "450"}};
  const chrome = {runtime: {id: "extension", onMessage: {addListener: fn => listeners.push(fn)},
    onMessageExternal: {addListener() {}}, onInstalled: {addListener() {}}},
  tabs: {get: async id => {
    if (!tabs.has(id)) throw new Error("No tab with id");
    return tabs.get(id);
  }},
  storage: {session: {get: async key => structuredClone({[key]: records[key]}),
    set: async values => Object.assign(records, structuredClone(values))}},
  scripting: {executeScript: async details => {
    // Chrome validates each args element before executing the function.
    // JSON.stringify silently turning undefined into null would miss this failure.
    for (const [index, arg] of details.args.entries()) {
      if (arg === undefined) throw new TypeError(`Error at property 'args': Error at index ${index}: Value is unserializable.`);
    }
    if (inspectionError) throw inspectionError;
    injections.push(details);
    return [{frameId: 0, documentId: "doc-1", result: {ok: true, selectedValue: "45759", optionToken: "option-1"}}];
  }}};
  const cache = new Map();
  const load = absolute => {
    if (cache.has(absolute)) return cache.get(absolute).exports;
    const module = {exports: {}}; cache.set(absolute, module);
    const source = ts.transpileModule(readFileSync(absolute, "utf8"), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
    }).outputText;
    const localRequire = specifier => {
      if (specifier.includes("configuration-capture/bridge")) return {handleCollectionTrial: async () => ({})};
      if (specifier.includes("vehicle-model-server")) return {requestModelCatalog: async query => {
        queries.push(query); return {ok: true, candidates: [candidate]};
      }};
      let target = path.resolve(path.dirname(absolute), specifier);
      if (!path.extname(target)) target += ".ts";
      assert.equal(existsSync(target), true, target);
      return load(target);
    };
    vm.runInNewContext(source, {chrome, URL, crypto: {randomUUID: () => "search-1"},
      module, exports: module.exports, require: localRequire}, {filename: absolute});
    return module.exports;
  };
  load(fileURLToPath(new URL("../src/entrypoints/background.ts", import.meta.url)));
  const send = (message, sender) => new Promise((resolve, reject) => {
    let pending = false;
    try {
      for (const listener of listeners) if (listener(message, sender, resolve) === true) pending = true;
      if (!pending) reject(new Error("No background listener handled the message"));
    } catch (error) { reject(error); }
  });
  return {send, tabs, queries, injections};
}

test("actual background entry accepts an SPA review session, rechecks offline config and inspects the exact document", async () => {
  const b = background();
  const searched = await b.send({type: "SEARCH_REVIEW_VEHICLE_MODELS", sourceTabId: 12, input, ...identity}, panel);
  assert.equal(searched.ok, true);
  const resolved = await b.send({type: "RESOLVE_REVIEW_MODEL_SEARCH", searchId: searched.searchId,
    modelId: "45759", ...identity}, content);
  assert.equal(resolved.ok, true, resolved.message);
  assert.equal(resolved.candidate.modelId, "45759");
  const rechecked = await b.send({type: "QUERY_VEHICLE_MODEL_CATALOG", input, expectedPageUrl: pageUrl}, content);
  assert.equal(rechecked.ok, true, rechecked.message);
  assert.equal(b.queries.length, 2);
  const target = {id: "45759", label: "完整车型名称", prepare: true};
  const inspected = await b.send({type: "INSPECT_REVIEW_SELECT", token: "token-1", expectedPageUrl: pageUrl, target}, content);
  assert.equal(inspected.ok, true, inspected.message);
  assert.deepEqual({...b.injections[0].target, documentIds: [...b.injections[0].target.documentIds]}, {tabId: 12, documentIds: ["doc-1"]});
  assert.equal(b.injections[0].world, "MAIN");
  assert.equal(b.injections[0].func.name, "inspectAntSelect");
  assert.equal(b.injections[0].args[1], pageUrl);
  // documentId 不存在时明确限定主 frame，不能默认广播到所有 frame。
  await b.send({type: "INSPECT_REVIEW_SELECT", token: "token-2", expectedPageUrl: pageUrl}, {...content, documentId: undefined});
  assert.deepEqual([...b.injections[1].target.frameIds], [0]);

  b.tabs.set(12, {url: "http://test.forjtruck.com/home"});
  for (const type of ["RESOLVE_REVIEW_MODEL_SEARCH", "QUERY_VEHICLE_MODEL_CATALOG", "INSPECT_REVIEW_SELECT"]) {
    const result = await b.send({type, searchId: searched.searchId, modelId: "45759", input, token: "token-3", ...identity}, content);
    assert.equal(result.ok, false);
    assert.equal(result.code, "PAGE_IDENTITY_CHANGED");
  }
  assert.equal(b.queries.length, 2);
  assert.equal(b.injections.length, 2);
});

test("reading the current selected ID omits the optional target at the actual scripting boundary", async () => {
  const b = background();
  const read = await b.send({type: "INSPECT_REVIEW_SELECT", token: "read-current", expectedPageUrl: pageUrl}, content);
  assert.equal(read.ok, true, read.message);
  assert.equal(read.selectedValue, "45759");
  assert.equal(b.injections.length, 1);
  assert.deepEqual(Array.from(b.injections[0].args), ["read-current", pageUrl]);
});

test("scripting failures retain their inspection stage and original cause", async () => {
  const b = background({inspectionError: new Error("Cannot access contents of the page")});
  const result = await b.send({type: "INSPECT_REVIEW_SELECT", token: "read-current", expectedPageUrl: pageUrl}, content);
  assert.equal(result.ok, false);
  assert.equal(result.code, "MODEL_SELECT_INSPECTION_FAILED");
  assert.match(result.message, /读取下拉框.*失败/);
  assert.match(result.message, /Cannot access contents of the page/);
});

test("background never passes iframe or extension-panel requests into main-world inspection", async () => {
  const b = background();
  for (const sender of [panel, {...content, frameId: 1}, {...content, id: "other"}]) {
    const denied = await b.send({type: "INSPECT_REVIEW_SELECT", expectedPageUrl: pageUrl, token: "token"}, sender);
    assert.equal(denied.ok, false);
    assert.equal(denied.code, "MODEL_PAGE_SOURCE_INVALID");
  }
  assert.equal(b.injections.length, 0);
});

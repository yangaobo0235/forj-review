import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const cache = new Map();

/**
 * 清空已加载的模块图。`loadModule` 会把第一次传入的全局桩缓存下来，同一个
 * 用例文件里的后续用例会复用它们，因此每个用例开头都要重新加载一遍。
 */
function freshModules() {
  cache.clear();
}

function loadModule(file, extraGlobals = {}, moduleOverrides = {}) {
  if (cache.has(file)) return cache.get(file).exports;
  const module = { exports: {} };
  cache.set(file, module);
  const output = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = (specifier) => {
    if (!specifier.startsWith(".")) return moduleOverrides[specifier] ?? require(specifier);
    let target = path.resolve(path.dirname(file), specifier);
    if (!path.extname(target)) target = [".ts", ".tsx"].map((extension) => target + extension).find(existsSync);
    return loadModule(target, extraGlobals, moduleOverrides);
  };
  vm.runInNewContext(output, {
    console,
    setTimeout,
    clearTimeout,
    AbortController,
    AbortSignal,
    DOMException,
    ...extraGlobals,
    require: localRequire,
    module,
    exports: module.exports,
  });
  return module.exports;
}

async function runReviewWithState(snapshot, collectionIssues = []) {
  freshModules();
  const state = [];
  const requests = [];
  const react = {
    useState(initial) {
      const index = state.length;
      state.push(initial);
      return [initial, (next) => { state[index] = typeof next === "function" ? next(state[index]) : next; }];
    },
    useCallback: (callback) => callback,
    useRef: (initial) => ({ current: initial }),
    useEffect: () => {},
  };
  const { useReviewWorkflow } = loadModule(
    fileURLToPath(new URL("../src/hooks/useReviewWorkflow.ts", import.meta.url)), {
      chrome: { tabs: {
        query: async () => [{ id: 42 }],
        sendMessage: async () => ({ businessType: "scrap_replacement", images: [], collectionIssues }),
      } },
      fetch: async (url, init) => {
        requests.push({ url: String(url), method: init?.method });
        return { ok: true, json: async () => String(url).endsWith("/jobs/urls")
          ? { job_id: "test-job" } : String(url).endsWith("/jobs/test-job") ? snapshot : {} };
      },
    }, { react },
  );
  await useReviewWorkflow("AUTO").startReview();
  return { error: state[4], notice: state[5], requests };
}

function workflowHarness(createResponse) {
  freshModules();
  const state = [], requests = [];
  const react = {
    useState(initial) { const index = state.length; state.push(initial); return [initial, next => {
      state[index] = typeof next === "function" ? next(state[index]) : next;
    }]; },
    useCallback: callback => callback, useEffect: () => {}, useRef: initial => ({ current: initial }),
  };
  const { useReviewWorkflow } = loadModule(fileURLToPath(new URL("../src/hooks/useReviewWorkflow.ts", import.meta.url)), {
    chrome: { tabs: { query: async () => [{ id: 42 }], sendMessage: async () => ({
      businessType: "scrap_replacement", pageFields: {}, images: [], collectionId: "collection-1",
    }) } },
    fetch: async (url, init) => {
      requests.push({ url: String(url), method: init?.method });
      return { ok: true, status: 200, json: async () => String(url).endsWith("/jobs/urls")
        ? await createResponse : String(url).endsWith("/jobs/job-1") && init?.method !== "DELETE"
          ? { status: "COMPLETED", progress: { total_count: 0, completed_count: 0 } } : {} };
    },
  }, { react });
  return { workflow: useReviewWorkflow("AUTO"), state, requests };
}

test("rapid repeated starts create exactly one review job", async () => {
  const { workflow, requests } = workflowHarness(Promise.resolve({ job_id: "job-1" }));
  await Promise.all([workflow.startReview(), workflow.startReview()]);
  assert.equal(requests.filter(request => request.method === "POST").length, 1);
});

test("reset discards a late created job and cancels it without restoring old state", async () => {
  let resolve;
  const pending = new Promise(done => { resolve = done; });
  const { workflow, state, requests } = workflowHarness(pending);
  const review = workflow.startReview();
  await new Promise(done => setTimeout(done, 0));
  assert.ok(requests.some(request => request.method === "POST"));
  workflow.reset();
  resolve({ job_id: "job-1" });
  await review;
  assert.equal(state[0], false);
  assert.equal(state[1], null);
  assert.equal(state[2], null);
  assert.equal(state[3], null);
  assert.equal(state[4], "");
  assert.ok(requests.some(request => request.method === "DELETE"));
});

test("partial completion notice survives an existing page collection warning", async () => {
  const { notice, error } = await runReviewWithState({
    status: "PARTIAL", progress: { total_count: 1, completed_count: 0, failed_count: 1, timed_out_count: 0 },
  }, ["候选资料超过 16 张，请确认是否漏审"]);
  assert.equal(error, "");
  assert.match(notice, /候选资料超过 16 张/);
  assert.match(notice, /部分材料未完成识别（失败 1 张）/);
  assert.match(notice, /查看原图人工核对/);
});

test("failed reviews give recovery guidance instead of showing backend exception names", async () => {
  const { error, requests } = await runReviewWithState({ status: "FAILED", message: "审核任务失败：AttributeError" });
  assert.match(error, /请重新审核.*人工核对.*联系管理员/);
  assert.doesNotMatch(error, /AttributeError/);
  assert.ok(requests.some(request => request.url.endsWith("/jobs/test-job") && request.method === "DELETE"));
});

test("URL review never reads, compresses, or uploads image bodies", async () => {
  freshModules();
  const messages = [];
  const requests = [];
  const page = {
    pageUrl: "https://example.test/review", collectionId: "url-collection",
    businessType: "scrap_replacement", region: "qingdao", profileVersion: "1.0",
    pageFields: {}, images: [{ index: 0, imageId: "url-0", src: "https://images.example.test/0.jpg" }],
  };
  const { useReviewWorkflow } = loadModule(
    fileURLToPath(new URL("../src/hooks/useReviewWorkflow.ts", import.meta.url)), {
      chrome: { tabs: {
        query: async () => [{ id: 42 }],
        sendMessage: async (_tabId, message) => {
          messages.push(message.type);
          assert.equal(message.type, "COLLECT_PAGE_MANIFEST");
          return page;
        },
      } },
      fetch: async (url, init) => {
        requests.push({ url: String(url), init });
        const result = String(url).endsWith("/jobs/urls") ? { job_id: "url-job" }
          : String(url).endsWith("/jobs/url-job")
            ? { status: "COMPLETED", progress: { completed_count: 1, total_count: 1 } } : {};
        return { ok: true, json: async () => result };
      },
    },
  );
  let captured;
  function Harness() { captured = useReviewWorkflow("AUTO"); return null; }
  renderToStaticMarkup(React.createElement(Harness));
  await captured.startReview();
  assert.deepEqual(messages, ["COLLECT_PAGE_MANIFEST"]);
  const jobRequests = requests.filter(request => request.url.includes("/jobs/"));
  assert.deepEqual(jobRequests.map(request => request.url.split("/jobs/")[1]), ["urls", "url-job"]);
  assert.equal(JSON.parse(jobRequests[0].init.body).images[0].src, page.images[0].src);
});

test("startReview surfaces the stale-collection reason instead of the business-detection fallback", async () => {
  freshModules();
  const jobPosts = [];
  const thrownMessages = [];
  class RecordingError extends Error {
    constructor(message) {
      super(message);
      thrownMessages.push(message);
    }
  }
  const stalePage = {
    pageUrl: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1",
    pageInstanceId: "page-instance",
    pageFingerprint: '[["application.id","case-a"]]',
    collectionId: "stale-collection",
    pageTitle: "报废置换审核",
    pageFields: {},
    pageText: "",
    images: [],
    businessType: "scrap_replacement",
    region: "qingdao",
    profileVersion: "1.0",
    staleCollection: true,
    collectionIssues: ["页面采集已过期，请重新采集"],
  };
  const chromeStub = {
    tabs: {
      query: async () => [{ id: 42 }],
      sendMessage: async (tabId, message) =>
        message.type === "COLLECT_PAGE_MANIFEST" ? stalePage : { ok: true },
    },
  };
  const fetchStub = async (url, init) => {
    if (String(url).endsWith("/api/review/jobs") && init?.method === "POST") {
      jobPosts.push(String(url));
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  const { useReviewWorkflow } = loadModule(
    fileURLToPath(new URL("../src/hooks/useReviewWorkflow.ts", import.meta.url)),
    {
      chrome: chromeStub,
      fetch: fetchStub,
      window: { setTimeout, clearTimeout },
      Error: RecordingError,
    },
  );

  let captured = null;
  function Harness() {
    captured = useReviewWorkflow("AUTO");
    return null;
  }
  renderToStaticMarkup(React.createElement(Harness));

  await captured.startReview();

  // 被取代的采集不得继续创建审核任务……
  assert.deepEqual(jobPosts, []);
  // ……并且面板透出真实原因，而不是“无法识别当前审核业务，请人工选择”。
  assert.deepEqual(thrownMessages, ["页面采集已过期，请重新采集"]);
});

test("侧边栏自己应用采集清单，字段和材料的中文名来自后端", async () => {
  // 侧边栏和 Content Script 是两个独立的运行时，Content Script 里应用的清单
  // 传不到面板。面板必须自己应用同一份，否则中文名会静默退回内置表。
  freshModules();
  const manifest = {
    business_type: "scrap_replacement",
    scopes: ["old_vehicle"],
    page_groups: [],
    fields: [
      {
        key: "old_vehicle.vin",
        label: "报废车辆车架号",
        aliases: ["报废车辆车架号"],
        section: "old_vehicle",
        section_required: true,
        reviewable: true,
      },
    ],
    materials: [
      { document_type: "invoice", label: "机动车销售发票", hints: ["发票"], slots: [] },
    ],
  };
  const stalePage = {
    pageUrl: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1",
    pageInstanceId: "page-instance",
    pageFingerprint: '[["application.id","case-a"]]',
    collectionId: "stale-collection",
    pageTitle: "报废置换审核",
    pageFields: {},
    pageText: "",
    images: [],
    businessType: "scrap_replacement",
    region: "qingdao",
    profileVersion: "1.0",
    staleCollection: true,
    collectionIssues: ["页面采集已过期，请重新采集"],
  };
  const fetchStub = async (url) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes("/api/review/collect-manifest") ? manifest : {},
  });

  const { useReviewWorkflow } = loadModule(
    fileURLToPath(new URL("../src/hooks/useReviewWorkflow.ts", import.meta.url)),
    {
      chrome: {
        tabs: {
          query: async () => [{ id: 42 }],
          sendMessage: async (tabId, message) =>
            message.type === "COLLECT_PAGE_MANIFEST" ? stalePage : { ok: true },
        },
      },
      fetch: fetchStub,
      window: { setTimeout, clearTimeout },
    },
  );
  const collectManifest = loadModule(
    fileURLToPath(new URL("../src/browser/collect-manifest.ts", import.meta.url)),
  );
  const { fieldLabel } = loadModule(
    fileURLToPath(new URL("../src/reviewPanelConfig.ts", import.meta.url)),
  );

  // 采集还没发生：面板只能退回内置表。
  assert.equal(collectManifest.manifestFieldLabels(), null);

  let captured = null;
  function Harness() {
    captured = useReviewWorkflow("AUTO");
    return null;
  }
  renderToStaticMarkup(React.createElement(Harness));
  await captured.startReview().catch(() => {});

  assert.equal(collectManifest.manifestFieldLabels()["old_vehicle.vin"], "报废车辆车架号");
  assert.equal(collectManifest.manifestMaterialLabels().invoice, "机动车销售发票");
  assert.equal(fieldLabel("old_vehicle.vin"), "报废车辆车架号");
});

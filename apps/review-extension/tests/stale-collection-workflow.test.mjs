import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test, afterEach, vi } from "vitest";
import { effectScope, ref } from "vue";


const scopes = [];
const catalog = JSON.parse(readFileSync(new URL("./fixtures/page-catalog.json", import.meta.url), "utf8"));
afterEach(() => { for (const scope of scopes.splice(0)) scope.stop(); });
function createWorkflow(useReviewWorkflow, selection = "AUTO") {
  const scope = effectScope(); scopes.push(scope);
  return scope.run(() => useReviewWorkflow(selection));
}

const mockedModules = new Set();
function freshModules() {
  vi.resetModules();
  vi.unstubAllGlobals();
  for (const module of mockedModules) vi.doUnmock(module);
  mockedModules.clear();
}

async function loadModule(file, extraGlobals = {}, moduleOverrides = {}) {
  for (const [name, value] of Object.entries(extraGlobals)) vi.stubGlobal(name, value);
  for (const [specifier, exports] of Object.entries(moduleOverrides)) {
    const target = fileURLToPath(new URL(`../src/features/review/model/${specifier}.ts`, import.meta.url));
    vi.doMock(target, () => exports);
    mockedModules.add(target);
  }
  return import(/* @vite-ignore */ file);
}

async function runReviewWithState(snapshot, collectionIssues = []) {
  freshModules();
  const requests = [];
  const { useReviewWorkflow } = await loadModule(
    fileURLToPath(new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url)), {
      chrome: { tabs: {
        query: async () => [{ id: 42 }],
        sendMessage: async () => ({ businessType: "scrap_replacement", images: [], collectionIssues }),
      } },
      fetch: async (url, init) => {
        requests.push({ url: String(url), method: init?.method });
        return { ok: true, json: async () => String(url).endsWith("/jobs/urls")
          ? { job_id: "test-job" } : String(url).endsWith("/jobs/test-job") ? snapshot : String(url).endsWith("/page-catalog") ? catalog : {} };
      },
    },
  );
  const workflow = createWorkflow(useReviewWorkflow);
  await workflow.startReview();
  return { error: workflow.error, notice: workflow.notice, requests };
}

async function workflowHarness(createResponse) {
  freshModules();
  const requests = [];
  const { useReviewWorkflow } = await loadModule(fileURLToPath(new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url)), {
    chrome: { tabs: { query: async () => [{ id: 42 }], sendMessage: async () => ({
      businessType: "scrap_replacement", pageFields: {}, images: [], collectionId: "collection-1",
    }) } },
    fetch: async (url, init) => {
      requests.push({ url: String(url), method: init?.method });
      return { ok: true, status: 200, json: async () => String(url).endsWith("/jobs/urls")
        ? await createResponse : String(url).endsWith("/jobs/job-1") && init?.method !== "DELETE"
          ? { status: "COMPLETED", progress: { total_count: 0, completed_count: 0 } } : String(url).endsWith("/page-catalog") ? catalog : {} };
    },
  });
  return { workflow: createWorkflow(useReviewWorkflow), requests };
}

test("rapid repeated starts create exactly one review job", async () => {
  const { workflow, requests } = await workflowHarness(Promise.resolve({ job_id: "job-1" }));
  await Promise.all([workflow.startReview(), workflow.startReview()]);
  assert.equal(requests.filter(request => request.method === "POST").length, 1);
});

test("offline service preserves recognized source and refresh recovers without changing business choice", async () => {
  freshModules();
  let online = false, posts = 0;
  const manifest = JSON.parse(readFileSync(new URL("./fixtures/collect-manifest-vehicle-source.json", import.meta.url), "utf8"));
  const {useReviewWorkflow} = await loadModule(fileURLToPath(new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url)), {
    chrome: {tabs: {query: async () => [{id: 42}], sendMessage: async () => ({businessType: "vehicle_source",
      region: "default", pageId: "vehicle_source:default", images: [], pageFields: {}, collectionId: "source"})}},
    fetch: async (url, init) => {
      if (!online) throw new Error("offline");
      if (init?.method === "POST") posts++;
      const value = String(url).endsWith("/page-catalog") ? catalog
        : String(url).includes("/collect-manifest") ? manifest
        : String(url).endsWith("/jobs/urls") ? {job_id: "job"}
        : {status: "COMPLETED", progress: {total_count: 0, completed_count: 0}};
      return {ok: true, json: async () => value};
    },
  });
  const workflow = createWorkflow(useReviewWorkflow);
  await workflow.refreshPage();
  assert.equal(workflow.pageData.businessType, "vehicle_source");
  assert.match(workflow.error, /审核服务未连接/);
  await workflow.startReview();
  assert.equal(posts, 0);
  assert.equal(workflow.pageData.businessType, "vehicle_source");
  online = true;
  await workflow.refreshPage();
  assert.equal(workflow.error, "");
  assert.equal(workflow.pageData.businessType, "vehicle_source");
  await workflow.startReview();
  assert.equal(posts, 1);
  assert.equal(workflow.error, "");
});

test("Vue selection watcher ignores an old preflight after the business is changed", async () => {
  freshModules();
  let resolveOld;
  const old = new Promise(done => { resolveOld = done; });
  const selected = ref("AUTO");
  const {useReviewWorkflow} = await loadModule(fileURLToPath(new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url)), {}, {
    "../api/collect-page": {collectPageData: selection => selection === "AUTO" ? old : Promise.resolve({collectionId: "new", images: []})},
  });
  const workflow = createWorkflow(useReviewWorkflow, selected);
  workflow.reset(); selected.value = "vehicle_source_default";
  await new Promise(done => setTimeout(done, 0));
  assert.equal(workflow.pageData.collectionId, "new");
  resolveOld({collectionId: "old", images: []});
  await new Promise(done => setTimeout(done, 0));
  assert.equal(workflow.pageData.collectionId, "new");
});

for (const grouped of [false, true]) {
  test(`late ${grouped ? "group" : "single"} fill receipt cannot change a newly collected page in Vue`, async () => {
    freshModules();
    let resolveFill;
    const pendingFill = new Promise(done => { resolveFill = done; });
    let current = {collectionId: "first", businessType: "vehicle_source", pageFields: {"vehicle.vin": "A"}, images: []};
    const {useReviewWorkflow} = await loadModule(fileURLToPath(new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url)), {}, {
      "../api/collect-page": {collectPageData: async () => current},
      "../../../platform/extension/page-action-client": {
        applyPageFieldValue: () => pendingFill, applyPageFieldGroupValue: () => pendingFill,
      },
      "../api/review-client": {createUrlReviewJob: async () => ({job_id: "job"}),
        fetchReviewJob: async () => ({status: "COMPLETED", progress: {total_count: 0}}),
        cancelReviewJob: async () => {}, completionNotice: () => ""},
    });
    const workflow = createWorkflow(useReviewWorkflow);
    await new Promise(done => setTimeout(done, 0));
    const fill = grouped ? workflow.applyPageFieldGroupValue(["vehicle.vin"], "B") : workflow.applyPageFieldValue("vehicle.vin", "B");
    workflow.reset();
    current = {...current, collectionId: "second", pageFields: {"vehicle.vin": "C"}};
    await workflow.startReview();
    resolveFill({ok: true, message: "回读成功"}); await fill;
    assert.equal(workflow.pageData.collectionId, "second");
    assert.equal(workflow.pageData.pageFields["vehicle.vin"], "C");
  });
}

test("reset discards a late created job and cancels it without restoring old state", async () => {
  let resolve;
  const pending = new Promise(done => { resolve = done; });
  const { workflow, requests } = await workflowHarness(pending);
  const review = workflow.startReview();
  await new Promise(done => setTimeout(done, 0));
  assert.ok(requests.some(request => request.method === "POST"));
  workflow.reset();
  resolve({ job_id: "job-1" });
  await review;
  assert.equal(workflow.loading, false);
  assert.equal(workflow.review, null);
  assert.equal(workflow.job, null);
  assert.equal(workflow.pageData, null);
  assert.equal(workflow.error, "");
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
  const { useReviewWorkflow } = await loadModule(
    fileURLToPath(new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url)), {
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
            ? { status: "COMPLETED", progress: { completed_count: 1, total_count: 1 } } : String(url).endsWith("/page-catalog") ? catalog : {};
        return { ok: true, json: async () => result };
      },
    },
  );
  const captured = createWorkflow(useReviewWorkflow);
  await captured.startReview();
  assert.ok(messages.length >= 1);
  assert.ok(messages.every(message => message === "COLLECT_PAGE_MANIFEST"));
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

  const { useReviewWorkflow } = await loadModule(
    fileURLToPath(new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url)),
    {
      chrome: chromeStub,
      fetch: fetchStub,
      window: { setTimeout, clearTimeout },
      Error: RecordingError,
    },
  );

  const captured = createWorkflow(useReviewWorkflow);

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
      String(url).includes("/api/review/collect-manifest") ? manifest : String(url).endsWith("/page-catalog") ? catalog : {},
  });

  const { useReviewWorkflow } = await loadModule(
    fileURLToPath(new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url)),
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
  const collectManifest = await loadModule(
    fileURLToPath(new URL("../src/platform/browser/catalog/collect-manifest.ts", import.meta.url)),
  );
  const { fieldLabel } = await loadModule(
    fileURLToPath(new URL("../src/features/review/model/review-panel.ts", import.meta.url)),
  );

  // 采集还没发生：面板只能退回内置表。
  assert.equal(collectManifest.manifestFieldLabels(), null);

  const captured = createWorkflow(useReviewWorkflow);
  await captured.startReview().catch(() => {});

  assert.equal(collectManifest.manifestFieldLabels()["old_vehicle.vin"], "报废车辆车架号");
  assert.equal(collectManifest.manifestMaterialLabels().invoice, "机动车销售发票");
  assert.equal(fieldLabel("old_vehicle.vin"), "报废车辆车架号");
});

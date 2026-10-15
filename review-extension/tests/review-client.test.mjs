import assert from "node:assert/strict";
import test from "node:test";

import {
  agentBaseUrl,
  cancelReviewJob,
  completionNotice,
  createUrlReviewJob,
  fetchReviewJob,
  localAgentBaseUrl,
  publicAgentBaseUrl,
  resolveAgentBaseUrl,
} from "../src/reviewClient.ts";

test("snapshot cancellation also interrupts a stalled JSON body", async () => {
  const controller = new AbortController();
  const result = fetchReviewJob("job", async () => ({ ok: true, status: 200, json: () => new Promise(() => {}) }), controller.signal);
  controller.abort();
  await assert.rejects(result, { name: "AbortError" });
});

test("cleanup cancellation absorbs a failed network request", async () => {
  await cancelReviewJob("job", async () => { throw new Error("network failure"); });
});

test("selects local and public Agent endpoints by build mode", () => {
  assert.equal(agentBaseUrl, localAgentBaseUrl);
  assert.equal(resolveAgentBaseUrl("development", undefined), localAgentBaseUrl);
  assert.equal(resolveAgentBaseUrl("public", undefined), publicAgentBaseUrl);
  assert.equal(
    resolveAgentBaseUrl("public", "http://example.test:9000/"),
    "http://example.test:9000",
  );
});

test("development requests ignore browser storage when selecting the review backend", async () => {
  const previousMode = Object.getOwnPropertyDescriptor(globalThis, "__REVIEW_AGENT_BUILD_MODE__");
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  try {
    Object.defineProperty(globalThis, "__REVIEW_AGENT_BUILD_MODE__", { configurable: true, value: "development" });
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() { throw new Error("Review backend selection must not read browser storage"); },
    });
    const client = await import("../src/reviewClient.ts?backend-selection");
    assert.equal(client.agentBaseUrl, localAgentBaseUrl);
  } finally {
    if (previousMode) Object.defineProperty(globalThis, "__REVIEW_AGENT_BUILD_MODE__", previousMode);
    else delete globalThis.__REVIEW_AGENT_BUILD_MODE__;
    if (previousStorage) Object.defineProperty(globalThis, "localStorage", previousStorage);
    else delete globalThis.localStorage;
  }
});

test("deadline notices distinguish available results from a missing conclusion", () => {
  assert.match(completionNotice({ status: "RUNNING" }), /120 秒/);
  assert.match(completionNotice({ status: "RUNNING" }), /尚未生成结论/);
  assert.match(completionNotice({ status: "RUNNING", result: {} }), /当前结果仅供参考/);
  assert.doesNotMatch(completionNotice({ status: "RUNNING" }), /以下为当前已完成结果/);
  assert.match(completionNotice({ status: "PARTIAL" }), /部分材料未完成识别/);
  assert.equal(completionNotice({ status: "COMPLETED" }), "");
});

test("partial completion separates failed recognition from timeouts", () => {
  const failed = completionNotice({ status: "PARTIAL", progress: { failed_count: 2, timed_out_count: 0 } });
  assert.match(failed, /失败 2 张/);
  assert.doesNotMatch(failed, /超时|时限/);
  assert.match(failed, /刷新原页面后重新审核/);
  assert.match(failed, /查看原图人工核对/);
  const mixed = completionNotice({ status: "PARTIAL", progress: { failed_count: 1, timed_out_count: 2 } });
  assert.match(mixed, /失败 1 张，超时 2 张/);
});

test("submits original signed image URLs once without image bodies", async () => {
  const requests = [];
  const src = "https://images.example.test/original.jpg?signature=synthetic%2Btoken&expires=123";
  await createUrlReviewJob({ pageId: "scrap-qingdao-v2", images: [{ index: 0, src, dataUrl: "data:image/jpeg;base64,AA==" }] },
    async (url, init) => {
      requests.push({ url, init });
      return new Response(JSON.stringify({ job_id: "url-job" }));
    });
  assert.equal(requests.length, 1);
  assert.match(requests[0].url, /\/jobs\/urls$/);
  assert.equal(requests[0].init.headers["Content-Type"], "application/json");
  const body = JSON.parse(requests[0].init.body);
  assert.equal(body.pageId, "scrap-qingdao-v2");
  assert.equal(body.images[0].src, src);
  assert.equal(body.images[0].dataUrl, undefined);
});

test("marks browser-only images unavailable without downloading them", async () => {
  let body;
  await createUrlReviewJob({ images: [{ index: 0, src: "blob:https://example.test/id" }] },
    async (_url, init) => {
      body = JSON.parse(init.body);
      return new Response(JSON.stringify({ job_id: "url-job" }));
    });
  assert.match(body.images[0].collectionError, /暂时无法读取.*刷新原页面.*查看原图人工核对/);
  assert.doesNotMatch(body.images[0].collectionError, /HTTP|blob:/);
  assert.equal(body.images[0].dataUrl, undefined);
});

test("service errors give operators a retry action", async () => {
  const fetcher = async () => new Response(null, { status: 503 });

  await assert.rejects(() => createUrlReviewJob({ images: [] }, fetcher), (error) => {
    assert.match(error.message, /稍后重新审核.*联系管理员/);
    assert.doesNotMatch(error.message, /503|HTTP/);
    return true;
  });
});

test("reports a missing job separately from other polling failures", async () => {
  const missingFetcher = async () => new Response(null, { status: 404 });
  const failedFetcher = async () => new Response(null, { status: 502 });

  await assert.rejects(() => fetchReviewJob("missing", missingFetcher), /任务已失效.*重新审核/);
  await assert.rejects(() => fetchReviewJob("failed", failedFetcher), /服务暂时不可用.*重新审核/);
});

test("network failures preserve diagnostics and give a recovery action for creation and polling", async () => {
  const cause = new TypeError("Failed to fetch");
  const offline = async () => { throw cause; };
  for (const request of [() => createUrlReviewJob({ images: [] }, offline), () => fetchReviewJob("job", offline)]) {
    await assert.rejects(request, (error) => {
      assert.match(error.message, /检查网络后重新审核/);
      assert.equal(error.cause, cause);
      return true;
    });
  }
});

test("access, invalid page data and busy errors have distinct recovery advice", async () => {
  for (const [status, advice] of [[401, /检查访问权限/], [403, /检查访问权限/], [422, /刷新原页面后重新审核/], [429, /繁忙.*稍后重新审核/]]) {
    const fetcher = async () => new Response(null, { status });
    await assert.rejects(() => createUrlReviewJob({ images: [] }, fetcher), advice);
  }
});

test("unreadable service responses do not expose JSON parser errors to operators", async () => {
  const invalidResponse = async () => new Response("temporary gateway page");
  for (const request of [() => createUrlReviewJob({ images: [] }, invalidResponse), () => fetchReviewJob("job", invalidResponse)]) {
    await assert.rejects(request, (error) => {
      assert.match(error.message, /结果暂时无法读取.*重新审核/);
      assert.ok(error.cause instanceof SyntaxError);
      return true;
    });
  }
});

import assert from "node:assert/strict";
import { test } from "vitest";
import {
  createModelPageVerifier,
  supportedModelPage,
} from "../src/platform/extension/vehicle-model-page.ts";
import { createModelBridgeHandler } from "../src/platform/extension/vehicle-model-bridge.ts";

const pageUrl = "http://test.forjtruck.com/consistency-qingdao?showPageModel=1";
const sender = {
  id: "extension",
  tab: { id: 12, url: "http://test.forjtruck.com/home" },
  frameId: 0,
  documentId: "document-1",
  url: "http://test.forjtruck.com/login",
  origin: "http://test.forjtruck.com",
};
const input = {
  announcementModel: "DFH4250A4",
  engineModel: "DCI450-51",
  pageScope: {
    brandId: "2",
    brandName: "东风商用车",
    seriesId: "3",
    seriesName: "示例车系",
    typeLevel: "牵引车",
  },
};

function harness() {
  const state = { tab: { url: pageUrl }, reads: [], queries: [], inspections: [] };
  const verify = createModelPageVerifier("extension", async (id) => {
    state.reads.push(id);
    return state.tab;
  });
  const bridge = createModelBridgeHandler(
    verify,
    async (query) => {
      state.queries.push(query);
      return { ok: true, candidates: [{ modelId: "45759" }] };
    },
    async (...args) => {
      state.inspections.push(args);
      return { ok: true, selectedValue: "45759" };
    },
  );
  return { state, verify, bridge };
}

test("SPA route authorization reads the actual source tab rather than sender.url or sender.tab.url", async () => {
  const { verify, state } = harness();
  assert.equal(await verify(sender, pageUrl), 12);
  assert.deepEqual(state.reads, [12]);
  assert.equal(
    supportedModelPage(sender.url),
    false,
    "old route check incorrectly rejected this valid review request",
  );
  assert.equal(supportedModelPage(state.tab.url), true);
  assert.equal(
    await verify({ ...sender, url: undefined }, pageUrl),
    12,
    "Chrome origin is sufficient when optional sender.url is absent",
  );
  for (const base of [
    "http://test.forjtruck.com",
    "https://test.forjtruck.com",
    "https://admin.forjtruck.com",
  ]) {
    for (const region of ["qingdao", "changchun"]) {
      const actualUrl = `${base}/consistency-${region}?showPageModel=1`;
      state.tab = { url: actualUrl };
      assert.equal(await verify({ ...sender, url: `${base}/login`, origin: base }, actualUrl), 12);
    }
  }
});

test("only actual unchanged main review tabs are permitted; foreign senders and frames fail before reading tabs", async () => {
  const { verify, state } = harness();
  for (const forged of [
    { ...sender, id: "other" },
    { ...sender, tab: undefined },
    { ...sender, tab: { id: -1 } },
    { ...sender, tab: { id: "12" } },
    { ...sender, frameId: 2 },
    { ...sender, frameId: undefined },
    { ...sender, url: "https://evil.test/login" },
    { ...sender, origin: "https://evil.test" },
    { ...sender, url: undefined, origin: undefined },
    { ...sender, url: "http://test.forjtruck.com.evil.test/login" },
  ]) {
    await assert.rejects(
      verify(forged, pageUrl),
      (error) => error.code === "MODEL_PAGE_SOURCE_INVALID",
    );
  }
  for (const invalid of [
    "http://test.forjtruck.com/login",
    "http://test.forjtruck.com/scrap-replace-qingdao",
    "http://test.forjtruck.com/consistency-qingdao-evil",
    "https://evil.test/consistency-qingdao",
    "",
  ]) {
    await assert.rejects(
      verify(sender, invalid),
      (error) => error.code === "MODEL_PAGE_SOURCE_INVALID",
    );
  }
  assert.deepEqual(state.reads, []);
});

test("current tab changes, pending navigation, missing permission and closed tabs return specific causes", async () => {
  const { verify, state } = harness();
  for (const tab of [
    { url: "http://test.forjtruck.com/home" },
    { url: pageUrl + "&record=2" },
    { url: pageUrl, pendingUrl: "http://test.forjtruck.com/login" },
  ]) {
    state.tab = tab;
    await assert.rejects(
      verify(sender, pageUrl),
      (error) => error.code === "PAGE_IDENTITY_CHANGED" && /已切换页面/.test(error.message),
    );
  }
  state.tab = {};
  await assert.rejects(verify(sender, pageUrl), /无法读取业务标签页当前地址/);
  const closed = createModelPageVerifier("extension", async () => {
    throw new Error("No tab with id");
  });
  await assert.rejects(
    closed(sender, pageUrl),
    (error) => error.code === "PAGE_IDENTITY_CHANGED" && /已关闭/.test(error.message),
  );
});

test("configuration recheck and exact option inspection both accept stale SPA source URLs", async () => {
  const { bridge, state } = harness();
  const query = await bridge(
    { type: "QUERY_VEHICLE_MODEL_CATALOG", expectedPageUrl: pageUrl, input },
    sender,
  );
  assert.equal(query.ok, true);
  assert.deepEqual(state.queries, [input]);
  const target = { id: "45759", label: "完整车型名称", prepare: true };
  const read = await bridge(
    { type: "INSPECT_REVIEW_SELECT", expectedPageUrl: pageUrl, token: "token-1", target },
    sender,
  );
  assert.equal(read.ok, true);
  assert.deepEqual(state.inspections, [[12, sender, "token-1", pageUrl, target]]);
  state.tab.url = "http://test.forjtruck.com/home";
  for (const type of ["QUERY_VEHICLE_MODEL_CATALOG", "INSPECT_REVIEW_SELECT"]) {
    const denied = await bridge(
      { type, expectedPageUrl: pageUrl, input, token: "token-2" },
      sender,
    );
    assert.equal(denied.ok, false);
    assert.equal(denied.code, "PAGE_IDENTITY_CHANGED");
  }
  assert.equal(state.queries.length, 1);
  assert.equal(state.inspections.length, 1);
});

test("malformed inspection parameters and invalid catalog inputs never reach page execution or service", async () => {
  const { bridge, state } = harness();
  for (const extra of [
    { token: 'bad"]' },
    { token: "" },
    { target: { id: "x", label: "车型", prepare: true } },
    { target: { id: "12", label: "车型", prepare: "yes" } },
    { target: { id: "12", label: "x".repeat(1001), prepare: true } },
  ]) {
    assert.equal(
      (
        await bridge(
          { type: "INSPECT_REVIEW_SELECT", expectedPageUrl: pageUrl, token: "token", ...extra },
          sender,
        )
      ).ok,
      false,
    );
  }
  assert.equal(
    (
      await bridge(
        {
          type: "QUERY_VEHICLE_MODEL_CATALOG",
          expectedPageUrl: pageUrl,
          input: { ...input, engineModel: "' OR 1=1" },
        },
        sender,
      )
    ).ok,
    false,
  );
  assert.equal(state.queries.length, 0);
  assert.equal(state.inspections.length, 0);
});

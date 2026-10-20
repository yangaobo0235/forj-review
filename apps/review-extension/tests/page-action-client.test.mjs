import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

import { applyPageFieldGroupValue, setAffiliation } from "../src/platform/extension/page-action-client.ts";

test("sends one authorized message for both fields in a composite write", async () => {
  const messages = [];
  const chromeApi = {
    tabs: {
      sendMessage: async (tabId, message) => {
        messages.push({ tabId, message });
        return { ok: true, message: "组合字段已同时回填并回读", actions: [
          { field: "invoice.code", status: "FILLED" },
          { field: "invoice.invoice_no", status: "FILLED" },
        ] };
      },
    },
  };
  const target = {
    tabId: 42,
    pageUrl: "https://admin.example.test/review/1",
    pageInstanceId: "page-a",
    pageFingerprint: "[[\"application.id\",\"case-a\"]]",
    collectionId: "page-a:3",
  };
  const action = {
    fields: ["invoice.code", "invoice.invoice_no"],
    value: "INV-001",
    expectedValues: { "invoice.code": "", "invoice.invoice_no": "" },
  };

  const result = await applyPageFieldGroupValue(action, target, chromeApi);

  assert.equal(result.ok, true);
  assert.deepEqual(messages[0], {
    tabId: 42,
    message: {
      type: "APPLY_PAGE_FIELD_GROUP_VALUE",
      action,
      expectedPageUrl: target.pageUrl,
      expectedPageInstanceId: target.pageInstanceId,
      expectedPageFingerprint: target.pageFingerprint,
      expectedCollectionId: target.collectionId,
    },
  });
});

test("retired affiliation write channel has no client, hook or content handler", () => {
  for (const file of ["../src/features/review/model/use-review-workflow.ts", "../src/platform/extension/page-action-client.ts", "../src/entrypoints/content.ts"]) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.doesNotMatch(source, /APPLY_PAGE_FILL_INTENT|applyAffiliationFill|applyPageFillIntent/);
  }
});

test("sets both scrap replacement affiliation fields through the guarded action channel", async () => {
  const messages = [];
  const target = {
    tabId: 7,
    pageUrl: "https://admin.example.test/scrap-replace-changchun",
    pageInstanceId: "page-b",
    pageFingerprint: "[[\"old_vehicle.vin\",\"VIN\"]]",
    collectionId: "page-b:1",
    value: "公司",
  };
  const chromeApi = { tabs: { sendMessage: async (tabId, message) => {
    messages.push({ tabId, message });
    return { ok: true, message: "已回读", actions: [] };
  } } };
  const result = await setAffiliation(target, chromeApi);
  assert.equal(result.ok, true);
  assert.equal(messages[0].tabId, 7);
  assert.equal(messages[0].message.type, "SET_AFFILIATION");
  assert.deepEqual(messages[0].message.action.fields, ["old_vehicle.affiliation", "new_vehicle.affiliation"]);
  assert.equal(messages[0].message.action.value, "公司");
});

test("passes a personal affiliation value through the same guarded action channel", async () => {
  const messages = [];
  const target = {
    tabId: 8,
    pageUrl: "https://admin.example.test/scrap-replace-qingdao",
    pageInstanceId: "page-c",
    pageFingerprint: "[[\"old_vehicle.vin\",\"VIN\"]]",
    collectionId: "page-c:1",
    value: "个人",
  };
  const chromeApi = { tabs: { sendMessage: async (tabId, message) => {
    messages.push({ tabId, message });
    return { ok: true, message: "已回读", actions: [] };
  } } };

  await setAffiliation(target, chromeApi);

  assert.equal(messages[0].message.action.value, "个人");
});

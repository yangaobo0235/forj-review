import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { applyPageFieldGroupValue } from "../src/pageFillClient.ts";

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
  for (const file of ["../src/hooks/useReviewWorkflow.ts", "../src/pageFillClient.ts", "../src/browser/content.ts"]) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.doesNotMatch(source, /APPLY_PAGE_FILL_INTENT|applyAffiliationFill|applyPageFillIntent/);
  }
});

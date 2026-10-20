import assert from "node:assert/strict";
import { test } from "vitest";

import { catalogBusinessChoices, manualBusinessSelection } from "../src/features/review/model/review-panel.ts";
import { applyCollectManifest, manifestHintType } from "../src/platform/browser/catalog/collect-manifest.ts";
import { applyPageCatalog } from "../src/platform/browser/catalog/page-catalog.ts";
import { ReviewBusinessScope } from "../src/platform/browser/collection/business-scope.ts";
import { ReviewBusinessDetector } from "../src/platform/browser/identity/business-detector.ts";

const identity = (pageId, path, version = "1.0") => ({
  business_type: "permit_review", region: "new_city", profile_version: version,
  page_id: pageId, title: "许可证审核", paths: [path], anchors: [],
});

test("new business and version 1.0 pages appear without hardcoded choices", () => {
  const choices = catalogBusinessChoices({ version: "1.0", identities: [identity("permit:a", "/permit")] });
  assert.equal(Object.keys(choices).length, 2);
  const key = Object.keys(choices).find(key => key !== "AUTO");
  assert.deepEqual(manualBusinessSelection(key), {
    businessType: "permit_review", region: "new_city", profileVersion: "1.0",
    workflowStage: "permit_review", pageId: "permit:a", selectionMode: "MANUAL",
  });
});

test("manual selection resolves the exact page rather than first same-business page", () => {
  applyPageCatalog({ version: "1.0", identities: [identity("permit:a", "/a", "2.0"), identity("permit:b", "/b", "3.0")] });
  const selected = {
    businessType: "permit_review", region: "new_city", profileVersion: "1.0",
    workflowStage: "permit_review", pageId: "permit:b", selectionMode: "MANUAL",
  };
  const result = ReviewBusinessDetector.resolve("https://admin.forjtruck.com/b", "", selected);
  assert.equal(result.business.pageId, "permit:b");
  assert.equal(result.business.profileVersion, "3.0");
  assert.equal(ReviewBusinessDetector.resolve("https://admin.forjtruck.com/a", "", selected).business, null);
  applyPageCatalog(null);
});

test("custom material types use the manifest without updating browser material tables", () => {
  applyCollectManifest({
    business_type: "permit_review", scopes: ["vehicle"], fields: [], page_groups: [],
    materials: [{ document_type: "custom_permit", label: "许可证", hints: ["许可证"], slots: [] }],
  });
  const type = manifestHintType("许可证正面");
  assert.equal(type, "custom_permit");
  assert.equal(ReviewBusinessScope.documentTypeFor("vehicle", 1, type), "custom_permit");
  applyCollectManifest(null);
});

test("invalid catalog keys cannot silently produce undefined business identities", () => {
  assert.throws(() => manualBusinessSelection("config:broken"), /标识无效/);
});

import assert from "node:assert/strict";
import { test } from "vitest";
import { applyCollectManifest, manifestWritableFields } from "../src/platform/browser/catalog/collect-manifest.ts";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import { ReviewBusinessScope } from "../src/platform/browser/collection/business-scope.ts";
import { classifyImage } from "../src/platform/browser/collection/material-hints.ts";
import { ReviewPageFieldWriter } from "../src/platform/browser/actions/page-field-writer.ts";
import { buildPageFingerprint } from "../src/platform/browser/identity/page-identity.ts";
import { applyPageCatalog, effectivePageCatalog } from "../src/platform/browser/catalog/page-catalog.ts";

test("clearing configuration removes historic collection and write permissions", async () => {
  applyCollectManifest(null);
  applyPageCatalog(null);
  assert.equal(ReviewPageFieldCollector.definitionForLabel("旧车车架号"), null);
  assert.equal(ReviewBusinessScope.scopeForLabel("旧车资料"), null);
  assert.equal(classifyImage("机动车行驶证"), "unknown");
  assert.equal(manifestWritableFields().size, 0);
  assert.equal(buildPageFingerprint({"application.id": "A", "old_vehicle.vin": "V"}), "");
  assert.deepEqual(effectivePageCatalog().identities, []);
  const control = {tagName: "INPUT", value: "OLD", isConnected: true, getAttribute: () => null};
  const result = await ReviewPageFieldWriter.executeValue({}, control, {field: "old_vehicle.vin", value: "NEW"});
  assert.equal(result.ok, false);
  assert.equal(control.value, "OLD");
});

test("a manifest without explicit write permissions cannot restore historic permissions", () => {
  assert.equal(applyCollectManifest({business_type: "test", scopes: [], fields: [], materials: [], page_groups: []}), true);
  assert.equal(manifestWritableFields().size, 0);
  assert.equal(buildPageFingerprint({"application.id": "A"}), "");
});

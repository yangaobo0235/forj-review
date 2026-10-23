import assert from "node:assert/strict";
import { test } from "vitest";
import {
  buildPageFingerprint,
  sameRecordExceptFields,
} from "../src/platform/browser/identity/page-identity.ts";
import {
  applyCollectManifest,
  manifestIdentityAnchors,
  manifestRecordIdentityFields,
  manifestWritableFields,
} from "../src/platform/browser/catalog/collect-manifest.ts";
import { manualBusinessSelection } from "../src/features/review/model/review-panel.ts";

test("declared record identifiers support ordinary order IDs and preserve zero", () => {
  assert.notEqual(buildPageFingerprint({ "order.id": 0 }, ["order.id"], ["order.id"]), "");
  assert.equal(
    buildPageFingerprint({ "order.owner": "甲公司" }, ["order.owner"], ["order.id"]),
    "",
  );
  assert.equal(buildPageFingerprint({ "vehicle.vin": "VIN" }, ["vehicle.vin"], []), "");
});

test("identity correction needs an independent unchanged identifier", () => {
  const fingerprint = (fields) =>
    buildPageFingerprint(
      fields,
      ["order.id", "vehicle.vin", "vehicle.owner"],
      ["order.id", "vehicle.vin"],
    );
  const expected = fingerprint({ "order.id": "A", "vehicle.vin": "V", "vehicle.owner": "甲" });
  assert.equal(
    sameRecordExceptFields(
      expected,
      fingerprint({ "order.id": "A", "vehicle.vin": "W", "vehicle.owner": "甲" }),
      ["vehicle.vin"],
      ["order.id", "vehicle.vin"],
    ),
    true,
  );
  for (const fields of [
    { "order.id": "B", "vehicle.vin": "W", "vehicle.owner": "甲" },
    { "vehicle.vin": "W", "vehicle.owner": "甲" },
  ]) {
    assert.equal(
      sameRecordExceptFields(
        expected,
        fingerprint(fields),
        ["vehicle.vin"],
        ["order.id", "vehicle.vin"],
      ),
      false,
    );
  }
  assert.equal(
    sameRecordExceptFields(
      expected,
      expected,
      ["order.id", "vehicle.vin"],
      ["order.id", "vehicle.vin"],
    ),
    false,
  );
  assert.equal(sameRecordExceptFields("invalid", expected, [], ["order.id"]), false);
});

test("invalid explicit identity declaration disables writes instead of enabling a legacy fallback", () => {
  const manifest = {
    business_type: "orders",
    scopes: [],
    fields: [],
    materials: [],
    page_groups: [],
    identity_anchors: ["order.id"],
    record_identity_fields: ["other.id"],
    writable_fields: ["order.total"],
  };
  assert.equal(applyCollectManifest(manifest), false);
  assert.deepEqual(manifestIdentityAnchors(), []);
  assert.deepEqual(manifestRecordIdentityFields(), []);
  assert.equal(manifestWritableFields().size, 0);
  assert.equal(applyCollectManifest({ ...manifest, record_identity_fields: [] }), true);
  assert.deepEqual(manifestRecordIdentityFields(), []);
  applyCollectManifest(null);
});

test("manual catalog choices preserve a stable page id when business and region are shared", () => {
  const choice = `config:${encodeURIComponent(JSON.stringify({ businessType: "consistency", region: "default", profileVersion: "2.0", pageId: "consistency-default-v2" }))}`;
  assert.deepEqual(manualBusinessSelection(choice), {
    businessType: "consistency",
    region: "default",
    profileVersion: "2.0",
    workflowStage: "consistency",
    selectionMode: "MANUAL",
    pageId: "consistency-default-v2",
  });
});

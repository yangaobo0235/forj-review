import assert from "node:assert/strict";
import { test } from "vitest";
import {
  captureConfigurationPage,
  trialConfigurationPage,
} from "../src/platform/browser/collection/configuration-discovery.ts";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import {
  applyCollectManifest,
  currentCollectManifest,
  manifestFieldDefinitions,
} from "../src/platform/browser/catalog/collect-manifest.ts";

const manifest = (key) => ({
  business_type: "permit_review",
  scopes: [],
  page_groups: [],
  materials: [],
  fields: [
    {
      key,
      label: "注册日期",
      aliases: ["注册日期"],
      section: "unknown",
      section_required: false,
      reviewable: true,
    },
  ],
  writable_fields: [],
  identity_anchors: [],
  record_identity_fields: [],
});

test("unconfigured discovery and trial bind to the same record and restore the formal manifest", async () => {
  const previousDiscover = ReviewPageFieldCollector.discover;
  const previousCollect = ReviewPageFieldCollector.collect;
  const oldDocument = globalThis.document,
    oldLocation = globalThis.location;
  let value = "2020-01-02";
  const element = {
    tagName: "INPUT",
    disabled: false,
    getAttribute: (name) => (name === "type" ? "date" : null),
    closest: () => null,
  };
  globalThis.location = { href: "https://example.test/new-page?id=synthetic" };
  globalThis.document = {
    title: "Synthetic fixture",
    images: [],
    querySelectorAll: () => [],
    body: { innerText: "注册日期", querySelectorAll: () => [] },
  };
  ReviewPageFieldCollector.discover = () => [
    { label: "注册日期", value, section: "unknown", source: "control", element },
  ];
  ReviewPageFieldCollector.collect = () => {
    assert.ok(manifestFieldDefinitions()["permit.registered_at"]);
    return {
      pageFields: { "permit.registered_at": value },
      ambiguousFields: [],
      fieldTargets: [{ field: "permit.registered_at", element }],
    };
  };
  const previous = manifest("old.date");
  applyCollectManifest(previous);
  try {
    const snapshot = await captureConfigurationPage();
    assert.equal(snapshot.fields.length, 1);
    assert.equal(snapshot.fields[0].control_kind, "date");
    assert.equal(snapshot.fields[0].candidate_id, "field_0");
    assert.equal(snapshot.fields[0].editable, true);
    element.readOnly = true;
    assert.equal((await captureConfigurationPage()).fields[0].editable, false);
    element.readOnly = false;
    const restored = await captureConfigurationPage();
    Object.assign(snapshot, restored);
    const task = {
      id: "trial",
      kind: "browser",
      snapshot_id: snapshot.snapshot_id,
      page_instance_id: snapshot.page_instance_id,
      page_url: snapshot.page_url,
      structure_digest: snapshot.structure_digest,
      candidate_digest: "candidate",
      manifest: manifest("permit.registered_at"),
      page_catalog: {
        version: "1.0",
        identities: [
          {
            business_type: "permit_review",
            region: "default",
            page_id: "new_page",
            paths: ["/new-page"],
            anchors: ["注册日期"],
          },
        ],
      },
    };
    const receipt = await trialConfigurationPage(task);
    assert.equal(receipt.fields["permit.registered_at"], 1);
    assert.equal(receipt.control_kinds["permit.registered_at"], "date");
    assert.equal(receipt.matched_page_id, "new_page");
    assert.equal(currentCollectManifest(), previous);
    value = "2030-01-02";
    await assert.rejects(trialConfigurationPage(task), /页面记录或结构已变化/);
    value = "2020-01-02";
    await assert.rejects(trialConfigurationPage({ ...task, snapshot_id: "old" }), /采集已失效/);
    ReviewPageFieldCollector.collect = () => {
      throw new Error("synthetic collection failure");
    };
    await assert.rejects(trialConfigurationPage(task), /synthetic collection failure/);
    assert.equal(currentCollectManifest(), previous);
  } finally {
    ReviewPageFieldCollector.discover = previousDiscover;
    ReviewPageFieldCollector.collect = previousCollect;
    applyCollectManifest(null);
    globalThis.document = oldDocument;
    globalThis.location = oldLocation;
  }
});

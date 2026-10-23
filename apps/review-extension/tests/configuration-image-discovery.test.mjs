// @vitest-environment jsdom
import { expect, test } from "vitest";
import {
  captureConfigurationPage,
  trialConfigurationPage,
} from "../src/platform/browser/collection/configuration-discovery.ts";
import { applyCollectManifest } from "../src/platform/browser/catalog/collect-manifest.ts";

test("unsupported data image stores no bytes or temporary URL and preserves read-only trial identity", async () => {
  const source = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB";
  document.body.innerHTML = `<figure>行驶证<img alt="行驶证" src="${source}"></figure>`;
  const image = document.querySelector("img");
  const rect = { width: 240, height: 180, left: 0, top: 0, right: 240, bottom: 180 };
  image.getClientRects = () => [rect];
  image.getBoundingClientRect = () => rect;
  Object.defineProperties(image, { naturalWidth: { value: 240 }, naturalHeight: { value: 180 } });
  const manifest = {
    business_type: "permit_review",
    scopes: [],
    fields: [],
    page_groups: [],
    materials: [{ document_type: "vehicle_license", label: "行驶证", hints: ["行驶证"] }],
    writable_fields: [],
    identity_anchors: [],
    record_identity_fields: [],
  };
  applyCollectManifest(manifest);
  try {
    const snapshot = await captureConfigurationPage();
    expect(snapshot.samples).toHaveLength(1);
    expect(snapshot.samples[0].url).toBe("");
    expect(snapshot.samples[0].source_kind).toBe("unsupported");
    expect(JSON.stringify(snapshot)).not.toContain("base64");
    const result = await trialConfigurationPage({
      id: "trial",
      kind: "browser",
      manifest,
      snapshot_id: snapshot.snapshot_id,
      page_instance_id: snapshot.page_instance_id,
      page_url: snapshot.page_url,
      structure_digest: snapshot.structure_digest,
      candidate_digest: "candidate",
    });
    expect(result.samples).toEqual({ sample_0: "vehicle_license" });
  } finally {
    applyCollectManifest(null);
    document.body.innerHTML = "";
  }
});

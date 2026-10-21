import { beforeEach } from "vitest";
import manifest from "./fixtures/collect-manifest.json";
import catalog from "./fixtures/page-catalog.json";
import { applyCollectManifest } from "../src/platform/browser/catalog/collect-manifest";
import { applyPageCatalog } from "../src/platform/browser/catalog/page-catalog";

beforeEach(() => {
  applyCollectManifest(manifest);
  applyPageCatalog(catalog);
});

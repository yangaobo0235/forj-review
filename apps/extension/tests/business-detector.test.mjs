import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { ReviewBusinessDetector } from "../src/platform/browser/identity/business-detector.ts";
import { applyPageCatalog } from "../src/platform/browser/catalog/page-catalog.ts";

// Synthetic states matching the attributes verified on the live Ant Design tabs.
const catalog = JSON.parse(readFileSync(new URL("./fixtures/page-catalog.json", import.meta.url), "utf8"));
const ready = label => ({ label, panelReady: true });
const choice = businessType => ({ businessType, region: "default", profileVersion: "1.0", workflowStage: businessType });
const sharedUrl = "https://admin.forjtruck.com/consistency-qingdao?showPageModel=1";

test.beforeEach(() => applyPageCatalog(catalog));
test.afterEach(() => applyPageCatalog(null));

test("scrap replacement keeps route recognition regardless of text or tabs", () => {
  for (const region of ["qingdao", "changchun"]) {
    assert.deepEqual(ReviewBusinessDetector.detect(`https://admin.forjtruck.com/scrap-replace-${region}?showPageModel=1`,
      "审核过户凭证 新车信息", ready("过户审核")), {
      businessType: "scrap_replacement", region, profileVersion: "1.0", workflowStage: "scrap_replacement",
      selectionMode: "AUTO", detectionStatus: "CONFIRMED",
    });
  }
});

test("all three review steps share both consistency routes and use the selected tab", () => {
  for (const region of ["qingdao", "changchun"]) {
    for (const [label, businessType] of [["车源审核", "vehicle_source"], ["过户审核", "transfer"], ["一致性审核", "consistency"]]) {
      const result = ReviewBusinessDetector.detect(`https://admin.forjtruck.com/consistency-${region}?showPageModel=1`,
        "车源审核 行驶证信息 审核过户凭证 过户发票买家名称 转入地车管所 新车信息 车辆所有人类型 新车挂靠", ready(label));
      assert.equal(result.businessType, businessType);
      assert.equal(result.workflowStage, businessType);
      assert.equal(result.region, "default");
      assert.equal(result.selectionMode, "AUTO");
    }
  }
});

test("text, a known route, and a manual choice cannot replace a valid active panel", () => {
  for (const state of [undefined, { label: "过户审核", panelReady: false }, ready("车源详情"), ready("过户详情"), ready("未知步骤")]) {
    assert.equal(ReviewBusinessDetector.detect(sharedUrl, "审核过户凭证 过户发票买家名称 转入地车管所", state), null);
    const result = ReviewBusinessDetector.resolve(sharedUrl, "", choice("transfer"), state);
    assert.equal(result.business, null);
    assert.ok(result.error);
  }
  assert.equal(ReviewBusinessDetector.detect("https://admin.forjtruck.com/vehicle-source", ""), null);
  assert.equal(ReviewBusinessDetector.detect("https://admin.forjtruck.com/vehicle-source", "", ready("车源审核")).businessType, "vehicle_source");
});

test("manual selections must agree with the selected review tab", () => {
  assert.match(ReviewBusinessDetector.resolve(sharedUrl, "", choice("transfer"), ready("车源审核")).error, /不一致/);
  const resolved = ReviewBusinessDetector.resolve(sharedUrl, "", choice("transfer"), ready("过户审核"));
  assert.equal(resolved.error, null);
  assert.equal(resolved.business.selectionMode, "MANUAL");
  assert.equal(resolved.business.businessType, "transfer");
  assert.match(ReviewBusinessDetector.resolve("https://admin.forjtruck.com/scrap-replace-changchun", "", {
    ...choice("scrap_replacement"), region: "qingdao",
  }).error, /不一致/);
});

test("diagnostics distinguish lists, reference tabs, and an unfinished panel transition", () => {
  assert.match(ReviewBusinessDetector.resolve(sharedUrl).error, /打开审核表单/);
  assert.match(ReviewBusinessDetector.resolve(sharedUrl, "", null, ready("过户详情")).error, /当前处于「过户详情」/);
  assert.match(ReviewBusinessDetector.resolve(sharedUrl, "", null, { label: "过户审核", panelReady: false }).error, /面板尚未显示/);
});

test("matching uses complete route segments and does not infer review steps on unknown paths", () => {
  assert.equal(ReviewBusinessDetector.detect("https://admin.forjtruck.com/scrap-replace-qingdao-old", ""), null);
  assert.equal(ReviewBusinessDetector.detect("https://admin.forjtruck.com/scrap-replace-qingdao/detail/1", "").region, "qingdao");
  assert.equal(ReviewBusinessDetector.detect("https://admin.forjtruck.com/consistency-qingdaox", "", ready("过户审核")), null);
  assert.equal(ReviewBusinessDetector.detect("https://admin.forjtruck.com/other", "车源审核 行驶证信息"), null);
});

test("ordinary configured pages keep anchor matching and manual fallback", () => {
  applyPageCatalog({ version: "1.0", identities: [{ business_type: "permit", region: "default", paths: ["/permit"], anchors: ["许可证"] }] });
  assert.equal(ReviewBusinessDetector.detect("https://example.test/renamed", "许可证").businessType, "permit");
  assert.equal(ReviewBusinessDetector.resolve("https://example.test/unknown", "", choice("permit")).business.selectionMode, "MANUAL");
  assert.match(ReviewBusinessDetector.resolve("https://example.test/unknown").error, /无法识别/);
});

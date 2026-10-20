import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

import {
  comparisonEvidence,
  evidenceHighlightPlans,
  evidencePresentation,
} from "../src/features/review/presentation/evidence.ts";

test("generic image evidence exposes a compact source label", () => {
  assert.deepEqual(
    evidencePresentation({ source: "Qwen", value: "￥ 220,000" }),
    { kind: "image", label: "图片证据", value: "￥ 220,000" },
  );
});

test("physical document evidence exposes stable source labels", () => {
  assert.deepEqual(
    evidencePresentation({ source: "图片识别", document_type: "invoice", business_scope: "transfer", value: "VIN-A" }),
    { kind: "image", label: "机动车销售发票", value: "VIN-A" },
  );
  assert.deepEqual(
    evidencePresentation({ source: "图片识别", document_type: "registration_certificate", business_scope: "transfer", group_order: 2, value: "VIN-A" }),
    { kind: "image", label: "机动车登记证书", value: "VIN-A" },
  );
});

test("scrap replacement keeps generic physical document labels", () => {
  assert.equal(
    evidencePresentation({ source: "图片识别", document_type: "invoice", business_scope: "new_vehicle", value: "VIN-A" }).label,
    "机动车销售发票",
  );
  assert.equal(
    evidencePresentation({ source: "图片识别", document_type: "registration_certificate", business_scope: "old_vehicle", group_order: 2, value: "VIN-A" }).label,
    "机动车登记证书",
  );
});

test("vehicle license evidence exposes the vehicle license source label", () => {
  assert.deepEqual(
    evidencePresentation({ source: "图片识别", document_type: "vehicle_license", value: "鲁A12345" }),
    { kind: "image", label: "行驶证", value: "鲁A12345" },
  );
});

test("application page evidence exposes a distinct label", () => {
  assert.deepEqual(
    evidencePresentation({ source: "申请页面字段", value: "220" }),
    { kind: "page", label: "申请页面", value: "220" },
  );
});

test("official qr evidence exposes a distinct label", () => {
  assert.deepEqual(
    evidencePresentation({ source: "二维码官网字段", value: "VIN-A" }),
    { kind: "page", label: "二维码官网", value: "VIN-A" },
  );
});

test("image value and focus action keep their columns without a thumbnail", () => {
  const css = (readFileSync(new URL("../src/app/App.css", import.meta.url), "utf8") + readFileSync(new URL("../src/features/review/ui/EvidenceReviewWorkbench.css", import.meta.url), "utf8"));

  assert.match(css, /\.image-evidence\s+\.evidence-value\s*{[^}]*grid-column:\s*2/s);
  assert.match(css, /\.image-evidence\s+\.evidence-focus\s*{[^}]*grid-column:\s*3/s);
});

test("comparison always puts the application page before image evidence", () => {
  const evidence = comparisonEvidence(
    [
      { source: "图片识别", image_id: "old_vehicle-01", value: "VIN-A" },
      { source: "图片识别", image_id: "old_vehicle-02", value: "VIN-B" },
    ],
    null,
  );

  assert.deepEqual(evidence.map((item) => [item.source, item.value]), [
    ["页面右侧字段", "未采集"],
    ["图片识别", "VIN-A"],
    ["图片识别", "VIN-B"],
  ]);
});

test("comparison uses right value when page evidence is absent", () => {
  const evidence = comparisonEvidence(
    [{ source: "图片识别", value: "VIN-A" }],
    "PAGE-VIN",
  );

  assert.deepEqual(evidence.at(0), {
    source: "页面右侧字段",
    value: "PAGE-VIN",
  });
});

test("comparison moves existing page evidence first without duplicating it", () => {
  const page = { source: "页面右侧字段", value: "PAGE-VIN" };
  const evidence = comparisonEvidence(
    [page, { source: "图片识别", value: "IMAGE-VIN" }],
    "IGNORED-RIGHT-VALUE",
  );

  assert.deepEqual(evidence, [
    page,
    { source: "图片识别", value: "IMAGE-VIN" },
  ]);
});

test("comparison orders transfer invoice before registration evidence", () => {
  const evidence = comparisonEvidence(
    [
      { source: "图片识别", document_type: "registration_certificate", value: "REG" },
      { source: "图片识别", document_type: "invoice", value: "INVOICE" },
    ],
    "PAGE",
  );
  assert.deepEqual(evidence.map((item) => item.value), ["PAGE", "INVOICE", "REG"]);
});

test("unique majority leaves majority sources unmarked and compares outliers to it", () => {
  assert.deepEqual(
    evidenceHighlightPlans([
      { source: "申请页面字段", value: "VIN-MAJORITY", conflicting: false },
      { source: "图片识别", value: "VIN-MAJORITY", conflicting: false },
      { source: "图片识别", value: "VIN-OUTLIER", conflicting: true },
    ]),
    [{}, {}, { compareTo: "VIN-MAJORITY" }],
  );
});

test("two conflicting sources compare against each other", () => {
  assert.deepEqual(
    evidenceHighlightPlans([
      { source: "申请页面字段", value: "VIN-A", conflicting: true },
      { source: "图片识别", value: "VIN-B", conflicting: true },
    ]),
    [{ compareTo: "VIN-B" }, { compareTo: "VIN-A" }],
  );
});

test("three conflicting sources without a majority are fully marked", () => {
  assert.deepEqual(
    evidenceHighlightPlans([
      { source: "申请页面字段", value: "VIN-A", conflicting: true },
      { source: "图片识别", value: "VIN-B", conflicting: true },
      { source: "图片识别", value: "VIN-C", conflicting: true },
    ]),
    [{ markAll: true }, { markAll: true }, { markAll: true }],
  );
});

test("comparison wires majority highlight plans instead of a fixed page baseline", () => {
  const source = readFileSync(
    new URL("../src/features/review/ui/ResultGroup.vue", import.meta.url),
    "utf8",
  );

  assert.match(source, /<strong>\{\{ statusLabel\(comparison\.status\) \}\}<\/strong>/);
  assert.doesNotMatch(source, /comparison\.message/);
  assert.match(source, /highlightPlans: evidenceHighlightPlans\(sourceValues\)/);
  assert.match(source, /:highlight-plan="highlightPlans\[index\]!"/);
  assert.doesNotMatch(source, /pageCompareTo/);
});

test("vin and plate evidence use position-based differences", () => {
  const source = readFileSync(
    new URL("../src/features/review/ui/ResultGroup.vue", import.meta.url),
    "utf8",
  );

  assert.match(source, /comparison\.field\.endsWith\("\.vin"\)/);
  assert.match(source, /comparison\.field\.endsWith\("\.plate_no"\)/);
  assert.match(readFileSync(new URL("../src/features/review/ui/EvidenceSource.vue", import.meta.url), "utf8"), /compareByPosition\s*\?\s*diffValueByPosition/);
});

test("page and image values use a readable value column", () => {
  const css = (readFileSync(new URL("../src/app/App.css", import.meta.url), "utf8") + readFileSync(new URL("../src/features/review/ui/EvidenceReviewWorkbench.css", import.meta.url), "utf8"));

  assert.match(
    css,
    /\.evidence-source\.page-evidence\s*{[^}]*grid-template-columns:\s*minmax\(90px, auto\) minmax\(0, 1fr\) auto/s,
  );
  assert.match(
    css,
    /\.page-evidence\s+\.evidence-value\s*{[^}]*grid-column:\s*2/s,
  );
});

test("conflicting evidence keeps the full value readable and soft-highlights differences", () => {
  const source = readFileSync(
    new URL("../src/features/review/ui/EvidenceSource.vue", import.meta.url),
    "utf8",
  );
  const css = (readFileSync(new URL("../src/app/App.css", import.meta.url), "utf8") + readFileSync(new URL("../src/features/review/ui/EvidenceReviewWorkbench.css", import.meta.url), "utf8"));

  assert.match(
    source,
    /evidence\.conflicting\s*\?\s*" evidence-conflicting"\s*:\s*""/s,
  );
  assert.match(
    css,
    /\.evidence-source\.evidence-conflicting\s+\.evidence-value\s*{[^}]*color:\s*var\(--review-text\);[^}]*font-weight:\s*500/s,
  );
  assert.match(css, /\.evidence-value mark\s*{[^}]*background:\s*#fee4e2;[^}]*color:\s*var\(--review-danger\)/s);
  assert.doesNotMatch(css, /\.evidence-value mark\s*{[^}]*display:\s*inline-block/s);
  assert.doesNotMatch(
    css,
    /\.evidence-source\.evidence-conflicting\s*{[^}]*(?:background|border)/s,
  );
});

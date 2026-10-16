import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { exceptionSections } from "../src/features/review/presentation/exception.ts";

const comparison = (field, status) => ({
  field,
  status,
  source: "test",
  evidence: [],
  message: "",
});

const sections = [
  { id: "old_vehicle", title: "报废车辆信息", fields: ["old.owner", "old.vin"] },
  { id: "new_vehicle", title: "新车信息", fields: ["new.owner"] },
  { id: "invoice", title: "发票信息", fields: ["invoice.amount"] },
];

test("keeps conflict and review-required comparisons in non-empty sections", () => {
  const result = exceptionSections(sections, [
    comparison("old.owner", "MATCH"),
    comparison("old.vin", "CONFLICT"),
    comparison("new.owner", "REVIEW_REQUIRED"),
    comparison("invoice.amount", "MATCH"),
  ]);

  assert.deepEqual(
    result.map((group) => ({
      id: group.section.id,
      fields: group.comparisons.map((item) => item.field),
      statuses: group.comparisons.map((item) => item.status),
    })),
    [
      { id: "old_vehicle", fields: ["old.vin"], statuses: ["CONFLICT"] },
      { id: "new_vehicle", fields: ["new.owner"], statuses: ["REVIEW_REQUIRED"] },
    ],
  );
});

test("returns no sections when every comparison matches", () => {
  const result = exceptionSections(sections, [
    comparison("old.owner", "MATCH"),
    comparison("new.owner", "MATCH"),
  ]);

  assert.deepEqual(result, []);
});

test("does not treat an unknown comparison status as an exception", () => {
  const result = exceptionSections(sections, [comparison("old.vin", "NEW_STATUS")]);

  assert.deepEqual(result, []);
});

test("keeps only transfer conflicts and insufficient fields", () => {
  const result = exceptionSections(
    [{
      id: "transfer",
      title: "过户凭证信息",
      fields: ["transfer.plate_no", "transfer.vin", "transfer.buyer_name"],
    }],
    [
      comparison("transfer.plate_no", "MATCH"),
      comparison("transfer.vin", "CONFLICT"),
      comparison("transfer.buyer_name", "REVIEW_REQUIRED"),
    ],
  );

  assert.deepEqual(
    result[0].comparisons.map((item) => item.field),
    ["transfer.vin", "transfer.buyer_name"],
  );
});

test("sidebar wiring preserves advice and qr while rendering exception groups", () => {
  const source = readFileSync(
    new URL("../src/features/review/ui/LegacyReviewResults.vue", import.meta.url),
    "utf8",
  );

  assert.match(source, /exceptionSections\(props\.review\.sections, props\.review\.comparisons\)/);
  assert.match(source, /exceptionGroups\.value\.map\(group =>/);
  assert.match(source, /:comparisons="group\.comparisons"/);
  assert.match(source, /字段校验未发现冲突或待复核项/);
  assert.match(source, /<ReviewAdvice/);
  assert.match(source, /<QrResults/);
  assert.match(source, /review\.business_type\s*===\s*["']scrap_replacement["']/);
});

test("field-check empty state is compact and subdued", () => {
  const css = (readFileSync(new URL("../src/app/App.css", import.meta.url), "utf8") + readFileSync(new URL("../src/features/review/ui/EvidenceReviewWorkbench.css", import.meta.url), "utf8"));

  assert.match(css, /\.field-check-empty\s*{[^}]*margin:\s*0/s);
  assert.match(css, /\.field-check-empty\s*{[^}]*color:\s*var\(--review-muted\)/s);
});

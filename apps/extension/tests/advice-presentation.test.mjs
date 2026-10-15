import assert from "node:assert/strict";
import test from "node:test";

import {
  checkStatusLabel,
  confidenceLabel,
  orderedCrossChecks,
  recommendationVisual,
} from "../src/features/review/presentation/advice.ts";

test("maps recommendations to accessible visual cues", () => {
  assert.deepEqual(recommendationVisual("PASS"), {
    symbol: "✓",
    label: "审核建议：通过",
  });
  assert.deepEqual(recommendationVisual("REVIEW_REQUIRED"), {
    symbol: "!",
    label: "审核建议：需要人工复核",
  });
});

test("maps check statuses to reviewer-facing labels", () => {
  assert.equal(checkStatusLabel("MATCH"), "满足");
  assert.equal(checkStatusLabel("CONFLICT"), "不满足");
  assert.equal(checkStatusLabel("INSUFFICIENT"), "无法校验");
});

test("labels confidence as image recognition confidence", () => {
  assert.equal(confidenceLabel(0.967), "图片识别平均置信度：97%");
  assert.equal(confidenceLabel(null), null);
});

test("returns only the cross-checks provided by the backend", () => {
  const rows = orderedCrossChecks("scrap_replacement", []);

  assert.deepEqual(rows, []);
});

test("preserves long source values without truncation", () => {
  const longValue = "某某省某某市超长企业名称有限责任公司".repeat(4);
  const [owner] = orderedCrossChecks("scrap_replacement", [{
    check_id: "CROSS-OWNER-001",
    label: "新旧车所有人一致性",
    status: "CONFLICT",
    reason: "不一致",
    values: [{ source: "旧车资料", value: longValue }],
  }]);

  assert.equal(owner.values[0].value, longValue);
});

test("does not add transfer cross-check placeholders", () => {
  const rows = orderedCrossChecks("transfer", []);

  assert.deepEqual(rows, []);
});

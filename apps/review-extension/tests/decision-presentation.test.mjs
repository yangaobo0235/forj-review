import ReviewStepDetail from "../src/features/review/ui/ReviewStepDetail.vue";
import assert from "node:assert/strict";
import { test } from "vitest";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";

function render(status, values, reason_codes = []) {
  return renderToString(
    h(ReviewStepDetail, {
      step: {
        step_id: "FIELD-vehicle.vin",
        category: "FIELD",
        label: "车架号",
        result_status: status,
        requires_reviewer_action: status !== "MATCH",
        reason: "核验说明",
        reason_codes,
        values,
        evidence: [],
      },
      qrChecks: [],
      disabled: false,
      onFocusImage: async () => {},
      onFill: async () => {},
      onChoose: () => {},
    }),
  );
}
const diff = [{ kind: "REPLACE", start: 0, end: 1, page_start: 0, page_end: 1, page_text: "A" }];

test("uncertain candidate differences stay orange inside a confirmed conflict task", async () => {
  const html = await render(
    "CONFLICT",
    [
      {
        source: "材料",
        value: "B",
        differences: diff,
        uncertain: true,
        conflicting: true,
        comparison_status: "INSUFFICIENT",
        check_reason: "疑似不一致",
      },
    ],
    ["VALUE_MISMATCH", "RECOGNITION_UNCERTAIN"],
  );
  assert.match(html, /status-conflict/);
  assert.match(html, /class="value-diff-pending"/);
  assert.match(html, /class="mismatch-pending"/);
  assert.doesNotMatch(html, /class="value-diff"|class="mismatch"/);
  assert.match(html, /比对结论待确认/);
  assert.match(html, /可靠证据存在差异/);
  assert.match(html, /识别不确定/);
});

test("confirmed per-value conclusion controls red highlighting independently of task merges", async () => {
  const html = await render("CONFLICT", [
    {
      source: "材料",
      value: "B",
      differences: diff,
      conflicting: true,
      comparison_status: "CONFLICT",
    },
  ]);
  assert.match(html, /class="value-diff"/);
  assert.match(html.replace(/<[^>]*>/g, ""), /与页面不一致/);
  assert.doesNotMatch(html, /value-diff-pending/);
});

test("policy review and missing evidence have distinct reason labels", async () => {
  const html = await render("INSUFFICIENT", [], ["POLICY_REVIEW", "EVIDENCE_MISSING"]);
  assert.match(html, /政策需人工确认/);
  assert.match(html, /证据不足/);
});

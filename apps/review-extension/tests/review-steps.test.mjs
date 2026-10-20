import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

import { isFieldFirstTaskVisible } from "../src/features/review/model/review-tasks.ts";

const steps = [
  {
    step_id: "BUSINESS-AFFILIATION-SUBJECT-001",
    sequence: 3,
    category: "BUSINESS_RULE",
    label: "主体关系",
    result_status: "CONFLICT",
    reason: "两家公司法人不一致",
    values: [{ source: "旧车主体", value: "甲运输有限公司" }],
    evidence: [],
  },
  {
    step_id: "BUSINESS-POLICY-ORIGIN-001",
    sequence: 1,
    category: "BUSINESS_RULE",
    label: "新车产地",
    result_status: "MATCH",
    reason: "产地符合当地政策",
    values: [],
    evidence: [],
  },
];



test("visibility follows the backend declaration rather than business field or task names", () => {
  const hidden = [
    { ...steps[0], step_id: "BUSINESS-AFFILIATION-SUBJECT-001" },
    { ...steps[0], step_id: "BUSINESS-AFFILIATION-AUX-CUSTOMER-NAME" },
    { ...steps[0], step_id: "FIELD-old_vehicle.affiliation", category: "FIELD", page_target_field: "old_vehicle.affiliation" },
  ];

  assert.equal(hidden.every((step) => isFieldFirstTaskVisible(step)), true);
  assert.equal(hidden.every((step) => !isFieldFirstTaskVisible({ ...step, visible: false })), true);
  assert.equal(isFieldFirstTaskVisible(steps[1]), true);
});

test("retired material tasks stay hidden while field and external failures remain visible", () => {
  const material = [
    { ...steps[0], step_id: "MATERIAL-GROUP", category: "MATERIAL" },
    { ...steps[0], step_id: "MATERIAL-MISSING_MATERIAL-1", category: "MATERIAL" },
    { ...steps[0], step_id: "BUSINESS-MATERIAL-COMPLETENESS", category: "BUSINESS_RULE" },
  ];

  for (const status of ["MATCH", "CONFLICT", "INSUFFICIENT"]) {
    assert.equal(material.every((step) => !isFieldFirstTaskVisible({ ...step, visible: true, result_status: status })), true);
  }
  for (const step_id of ["FIELD-old_vehicle.vin", "BUSINESS-QR-001", "BUSINESS-OWNER-001", steps[1].step_id]) {
    assert.equal(isFieldFirstTaskVisible({ ...steps[1], step_id }), true);
  }
});

test("keeps step text readable in a narrow panel", () => {
  const stylesheet = (readFileSync(new URL("../src/app/App.css", import.meta.url), "utf8") + readFileSync(new URL("../src/features/review/ui/EvidenceReviewWorkbench.css", import.meta.url), "utf8"));

  // 侧边栏只有几百像素宽，长理由/长字段值必须能断行，否则会横向溢出。
  // 断言只能落在 CSS 文本上——这里没有布局引擎，样式本身没法做行为测试。
  // 注意选择器必须指向**实际渲染**的容器：旧版本断言的是已退役的 `.stepper-*`
  // （没有任何组件再产出那个类名），那条断言永远不会失败，也保护不了真实布局。
  assert.match(stylesheet, /\.workbench-reason\s*{[^}]*overflow-wrap:\s*anywhere/);
  assert.match(stylesheet, /\.workbench-index span\s*{[^}]*overflow-wrap:\s*anywhere/);
  assert.match(stylesheet, /\.workbench-card-title h2\s*{[^}]*overflow-wrap:\s*anywhere/);
});

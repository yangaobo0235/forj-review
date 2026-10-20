import assert from "node:assert/strict";
import { test } from "vitest";
import { ReviewBusinessScope } from "../src/platform/browser/collection/business-scope.ts";

function loadBusinessScope() {
  return ReviewBusinessScope;
}

test("assigns old and new images from page order with independent group numbering", () => {
  const { assign } = loadBusinessScope();
  const assigned = assign([
    { kind: "label", text: "营业执照" },
    { kind: "image", index: 1 },
    { kind: "label", text: "报废车辆资料" },
    { kind: "image", index: 3 },
    { kind: "image", index: 4 },
    { kind: "label", text: "新车资料" },
    { kind: "image", index: 6 },
  ]);

  assert.deepEqual(Array.from(assigned, (item) => ({ ...item })), [
    { index: 1, businessScope: "business_license", groupTitle: "营业执照", groupOrder: 1, imageId: "business_license-01" },
    { index: 3, businessScope: "old_vehicle", groupTitle: "报废车辆资料", groupOrder: 1, imageId: "old_vehicle-01" },
    { index: 4, businessScope: "old_vehicle", groupTitle: "报废车辆资料", groupOrder: 2, imageId: "old_vehicle-02" },
    { index: 6, businessScope: "new_vehicle", groupTitle: "新车资料", groupOrder: 1, imageId: "new_vehicle-01" },
  ]);
});

test("recognizes business licenses as review evidence", () => {
  assert.deepEqual(
    { ...loadBusinessScope().scopeForLabel("营业执照") },
    { scope: "business_license", title: "营业执照" },
  );
});

test("recognizes identity headings as conditional review evidence and ignores container text", () => {
  const { scopeForLabel } = loadBusinessScope();

  assert.deepEqual({ ...scopeForLabel("身份证正面") }, { scope: "identity", title: "身份证正面" });
  assert.equal(scopeForLabel("报废车辆资料 新车资料"), null);
});

test("does not infer a retired transfer scope", () => {
  const assigned = loadBusinessScope().assign([
    { kind: "label", text: "过户资料" },
    { kind: "image", index: 2 },
    { kind: "image", index: 3 },
  ]);

  assert.deepEqual(Array.from(assigned, (item) => ({ ...item })), [
    { index: 2, businessScope: "unknown", groupTitle: "未分类资料", groupOrder: 1, imageId: "unknown-01" },
    { index: 3, businessScope: "unknown", groupTitle: "未分类资料", groupOrder: 2, imageId: "unknown-02" },
  ]);
});

test("scrap image order never substitutes for a physical material label", () => {
  const { documentTypeFor } = loadBusinessScope();

  for (const scope of ["old_vehicle", "new_vehicle"]) {
    for (const order of [1, 2, 3, 4]) assert.equal(documentTypeFor(scope, order, scope), "unknown");
  }
  assert.equal(documentTypeFor("old_vehicle", 3, "vehicle_license"), "vehicle_license");
  assert.equal(documentTypeFor("identity", 1, "id_card"), "identity_card");
});

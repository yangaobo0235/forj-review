import assert from "node:assert/strict";
import { test } from "vitest";

import { evidencePresentation } from "../src/features/review/presentation/evidence.ts";

test("generic image evidence exposes a compact source label", () => {
  assert.deepEqual(evidencePresentation({ source: "Qwen", value: "￥ 220,000" }), {
    kind: "image",
    label: "图片证据",
    value: "￥ 220,000",
  });
});

test("physical document evidence exposes stable source labels", () => {
  assert.deepEqual(
    evidencePresentation({
      source: "图片识别",
      document_type: "invoice",
      business_scope: "transfer",
      value: "VIN-A",
    }),
    { kind: "image", label: "机动车销售发票", value: "VIN-A" },
  );
  assert.deepEqual(
    evidencePresentation({
      source: "图片识别",
      document_type: "registration_certificate",
      business_scope: "transfer",
      group_order: 2,
      value: "VIN-A",
    }),
    { kind: "image", label: "机动车登记证书", value: "VIN-A" },
  );
});

test("scrap replacement keeps generic physical document labels", () => {
  assert.equal(
    evidencePresentation({
      source: "图片识别",
      document_type: "invoice",
      business_scope: "new_vehicle",
      value: "VIN-A",
    }).label,
    "机动车销售发票",
  );
  assert.equal(
    evidencePresentation({
      source: "图片识别",
      document_type: "registration_certificate",
      business_scope: "old_vehicle",
      group_order: 2,
      value: "VIN-A",
    }).label,
    "机动车登记证书",
  );
});

test("vehicle license evidence exposes the vehicle license source label", () => {
  assert.deepEqual(
    evidencePresentation({
      source: "图片识别",
      document_type: "vehicle_license",
      value: "鲁A12345",
    }),
    { kind: "image", label: "机动车行驶证或车辆资料", value: "鲁A12345" },
  );
});

test("application page evidence exposes a distinct label", () => {
  assert.deepEqual(evidencePresentation({ source: "申请页面字段", value: "220" }), {
    kind: "page",
    label: "申请页面",
    value: "220",
  });
});

test("official qr evidence exposes a distinct label", () => {
  assert.deepEqual(evidencePresentation({ source: "二维码官网字段", value: "VIN-A" }), {
    kind: "page",
    label: "二维码官网",
    value: "VIN-A",
  });
});

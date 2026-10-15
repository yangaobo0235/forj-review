import assert from "node:assert/strict";
import test from "node:test";

import {
  businessLabels,
  fieldLabel,
  groupStatusLabel,
  manualBusinessSelection,
  statusLabel,
} from "../src/features/review/model/review-panel.ts";

test("builds stable manual business metadata", () => {
  assert.equal(manualBusinessSelection("scrap_replacement_qingdao").region, "qingdao");
  assert.equal(manualBusinessSelection("scrap_replacement_changchun").region, "changchun");
  // 一致性和过户都不分地区：青岛、长春两个地址同一套规则，只有默认地区一项。
  assert.equal(manualBusinessSelection("consistency").businessType, "consistency");
  assert.equal(manualBusinessSelection("consistency").region, "default");
  assert.equal(manualBusinessSelection("transfer").businessType, "transfer");
  assert.equal(manualBusinessSelection("transfer").region, "default");
});

test("offers a manual choice for every business sharing an address", () => {
  // 共用地址的两个业务都要在下拉里，否则自动识别认不出来时没有退路。
  for (const choice of ["consistency", "transfer"]) {
    assert.ok(businessLabels[choice], choice);
  }
});

test("presents stable business, field, and status labels", () => {
  assert.equal(businessLabels.AUTO, "自动识别");
  assert.equal(businessLabels.scrap_replacement_changchun, "长春报废置换审核");
  assert.equal(fieldLabel("old_vehicle.vin"), "报废车辆车架号");
  assert.equal(fieldLabel("unknown.field"), "unknown.field");
  assert.equal(statusLabel("REVIEW_REQUIRED"), "待复核");
  assert.equal(groupStatusLabel("PARTIAL"), "部分完成");
});

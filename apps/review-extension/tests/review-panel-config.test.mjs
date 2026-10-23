import assert from "node:assert/strict";
import { test } from "vitest";

import {
  businessLabels,
  catalogBusinessChoices,
  fieldLabel,
  manualBusinessSelection,
} from "../src/features/review/model/review-panel.ts";

test("builds stable manual business metadata", () => {
  assert.equal(manualBusinessSelection("scrap_replacement_qingdao").region, "qingdao");
  assert.equal(manualBusinessSelection("scrap_replacement_changchun").region, "changchun");
  // 一致性审核的两个地区入口共用默认地区。
  assert.equal(manualBusinessSelection("consistency").businessType, "consistency");
  assert.equal(manualBusinessSelection("consistency").region, "default");
});

test("keeps the remaining unconfigured consistency choice", () => {
  for (const choice of ["consistency"]) {
    assert.ok(businessLabels[choice], choice);
  }
});

test("presents stable business and field labels", () => {
  assert.equal(businessLabels.AUTO, "自动识别");
  assert.equal(businessLabels.scrap_replacement_changchun, "长春报废置换审核");
  assert.equal(fieldLabel("old_vehicle.vin"), "报废车辆车架号");
  assert.equal(fieldLabel("unknown.field"), "unknown.field");
});

test("服务目录缺失时只提供自动识别占位，不展示历史页面版本", () => {
  assert.deepEqual(catalogBusinessChoices(null), { AUTO: "自动识别" });
});

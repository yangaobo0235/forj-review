/**
 * 证据卡上的材料中文名。
 *
 * 以前 `evidencePresentation` 用 `document_type` 硬编码了一份材料名，绕过了
 * 后端下发的采集清单。后果是同一份材料在同一块面板上有两个名字（清单说
 * 「机动车行驶证」，证据卡写死成「行驶证」），而新声明的材料类型
 * 一律显示成「图片证据」。本文件锁定
 * 「以清单为准、清单不可用时才退回内置表」。
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

import { applyCollectManifest } from "../src/platform/browser/catalog/collect-manifest.ts";
import { evidencePresentation } from "../src/features/review/presentation/evidence.ts";

const labelOf = (documentType) =>
  evidencePresentation({ source: "图片识别", document_type: documentType, value: "X" })
    .label;

test("清单不可用时退回内置表；内置表不认识的材料不编造名字", () => {
  assert.equal(labelOf("vehicle_license"), "行驶证");
  assert.equal(labelOf("registration_certificate"), "机动车登记证书");
  assert.equal(labelOf("permit"), "图片证据");
  assert.equal(labelOf(undefined), "图片证据");
});

test("清单应用后材料名以清单为准，含内置表不认识的新材料类型", () => {
  const manifest = JSON.parse(
    readFileSync(
      new URL("./fixtures/collect-manifest-synthetic.json", import.meta.url),
      "utf8",
    ),
  );
  assert.equal(applyCollectManifest(manifest), true);

  // 合成材料名称不在内置表里。
  assert.equal(labelOf("permit"), "许可证");
  // 同一份材料，清单的名字与内置表不同——这正是以前两个卡片叫法不一致的原因。
  assert.equal(labelOf("vehicle_license"), "自定义行驶证");
});

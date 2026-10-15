/**
 * 页面适配器注册表：全应用的页面识别入口。
 *
 * 识别规则在各适配器里，`business-detector` 只做识别入口和人工/自动冲突判定。
 */
import assert from "node:assert/strict";
import test from "node:test";

import { buildPageAdapterRegistry } from "../src/platform/browser/adapters/index.ts";
import { ReviewBusinessDetector } from "../src/platform/browser/identity/business-detector.ts";

const registry = buildPageAdapterRegistry();

test("地址唯一的页面解析到自己的适配器", () => {
  const cases = [
    ["https://admin.forjtruck.com/scrap-replace-qingdao", "scrap-replacement"],
    ["https://admin.forjtruck.com/scrap-replace-changchun?showPageModel=1", "scrap-replacement"],
    ["https://admin.forjtruck.com/vehicle-source", "vehicle-source"],
  ];

  for (const [url, expected] of cases) {
    assert.equal(registry.resolve(url)?.id, expected, url);
  }
});

test("共用地址的页面在适配器层不判定", () => {
  // 一致性审核与过户审核共用 /consistency-qingdao、/consistency-changchun，
  // 只看地址必然认错。判定交给后端下发的页面识别清单（见
  // `business-detector.test.mjs`）；适配器在这里必须说「不知道」，不能给答案。
  assert.equal(registry.resolve("https://admin.forjtruck.com/consistency-qingdao"), null);
  assert.equal(registry.resolve("https://admin.forjtruck.com/consistency-changchun"), null);
});

test("未登记的页面不解析到任何适配器", () => {
  assert.equal(registry.resolve("https://admin.forjtruck.com/unknown-page"), null);
  assert.equal(registry.selection("not a url"), null);
});

test("页面识别按完整路径段匹配，相似前缀不算命中", () => {
  // 相似前缀不能互相误判：/scrap-replace-qingdao-old 不是审核页。
  assert.equal(registry.resolve("https://admin.forjtruck.com/scrap-replace-qingdao-old"), null);
  assert.equal(registry.resolve("https://admin.forjtruck.com/consistency-qingdaox"), null);
});

test("注册表给出的业务选择与识别入口一致", () => {
  const url = "https://admin.forjtruck.com/scrap-replace-changchun?showPageModel=1";

  assert.deepEqual(registry.selection(url), ReviewBusinessDetector.detect(url, ""));
  assert.deepEqual(ReviewBusinessDetector.detect(url, ""), {
    businessType: "scrap_replacement",
    region: "changchun",
    profileVersion: "1.0",
    workflowStage: "scrap_replacement",
    selectionMode: "AUTO",
    detectionStatus: "CONFIRMED",
  });
});

test("人工选择的地区与页面不一致时拒绝执行", () => {
  const resolution = ReviewBusinessDetector.resolve(
    "https://admin.forjtruck.com/scrap-replace-changchun",
    "",
    {
      businessType: "scrap_replacement",
      region: "qingdao",
      profileVersion: "1.0",
      workflowStage: "scrap_replacement",
      selectionMode: "MANUAL",
    },
  );

  assert.equal(resolution.business, null);
  assert.match(resolution.error, /不一致/);
});

test("适配器只负责页面识别，不重复声明字段和写回逻辑", () => {
  // 字段别名、页面分区、图片筛选和写回均来自清单/通用写回器；适配器不该再有一份。
  const scrap = registry.resolve("https://admin.forjtruck.com/scrap-replace-qingdao");
  assert.ok(scrap);

  const source = scrap.constructor.toString() + scrap.detect.toString();
  assert.equal(source.includes("FIELD_DEFINITIONS"), false);
  assert.equal(source.includes("aliases"), false);
  assert.equal(source.includes("writeField"), false);
});

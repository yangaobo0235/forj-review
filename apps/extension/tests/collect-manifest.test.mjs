/**
 * 后端下发的采集清单与前端内置表的一致性。
 *
 * fixture 由后端 `build_collect_manifest` 生成（见 REFACTOR-PLAN 阶段 5）。
 * 这组用例通过前端真实的查找路径（`definitionForLabel` / `scopeForLabel`）
 * 验证：清单里的每个别名和每个页面分组标题，前端都能解析到相同的结果。
 *
 * 清单与内置表一致之后，才可以把内置表换成清单驱动。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  applyCollectManifest,
  manifestFieldDefinitions,
  manifestHintType,
  manifestMaterialLabels,
} from "../src/platform/browser/catalog/collect-manifest.ts";
import { ReviewBusinessScope } from "../src/platform/browser/collection/business-scope.ts";
import { ReviewImageCandidates } from "../src/platform/browser/collection/image-candidates.ts";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import { fieldLabel } from "../src/features/review/model/review-panel.ts";

const manifest = JSON.parse(
  readFileSync(new URL("./fixtures/collect-manifest.json", import.meta.url), "utf8"),
);

/** 别名 → 声明了该别名的字段键。多个字段共用别名时前端必须判为歧义。 */
const ownersByAlias = () => {
  const owners = new Map();
  for (const field of manifest.fields) {
    for (const alias of field.aliases) {
      owners.set(alias, [...(owners.get(alias) || []), field.key]);
    }
  }
  return owners;
};

test("清单里的每个字段别名都能被前端解析到同一个字段", () => {
  const owners = ownersByAlias();

  for (const field of manifest.fields) {
    for (const alias of field.aliases) {
      const resolved = ReviewPageFieldCollector.definitionForLabel(alias);
      const candidates = owners.get(alias);

      if (candidates.length > 1) {
        assert.equal(resolved, null, `${alias} 被多个字段共用时应判为歧义`);
        continue;
      }

      assert.equal(resolved?.field, field.key, `别名 ${alias} 解析到了别的字段`);
      assert.deepEqual(resolved.aliases, field.aliases, `别名 ${alias} 的候选列表不一致`);
      assert.equal(resolved.section, field.section, `别名 ${alias} 的页面区域不一致`);
      assert.equal(
        Boolean(resolved.sectionRequired),
        field.section_required,
        `别名 ${alias} 的区域必填标记不一致`,
      );
      assert.equal(
        resolved.reviewable === false,
        field.reviewable === false,
        `别名 ${alias} 的可核验标记不一致`,
      );
    }
  }
});

test("清单里的每个页面分组标题都能被前端解析到同一个业务分区", () => {
  for (const group of manifest.page_groups) {
    const resolved = ReviewBusinessScope.scopeForLabel(group.label);

    assert.equal(resolved?.scope, group.scope, `分组标题 ${group.label} 的分区不一致`);
    assert.equal(resolved?.title, group.title, `分组标题 ${group.label} 的标题不一致`);
  }
});

test("清单覆盖了前端会采集的全部字段", () => {
  const collected = manifest.fields.filter((field) => field.aliases.length > 0);

  assert.ok(collected.length >= 20, "清单里的可采集字段过少");
  for (const field of collected) {
    // 别名与字段标签一致，说明清单是从声明直接生成的，没有手写偏差。
    assert.equal(field.aliases[0], field.label, field.key);
  }
});


test("应用清单后前端走清单，结构不完整时退回内置表", () => {
  assert.equal(applyCollectManifest(manifest), true);
  assert.ok(manifestFieldDefinitions());

  // 清单里的字段仍然解析到同一个字段。
  assert.equal(ReviewPageFieldCollector.definitionForLabel("报废车辆类型")?.field, "old_vehicle.type");
  assert.equal(ReviewBusinessScope.scopeForLabel("报废车辆资料")?.scope, "old_vehicle");

  // 结构不完整时清空并返回 false，调用方继续用内置表。
  assert.equal(applyCollectManifest({ business_type: "x" }), false);
  assert.equal(manifestFieldDefinitions(), null);
  assert.equal(ReviewPageFieldCollector.definitionForLabel("报废车辆类型")?.field, "old_vehicle.type");
  assert.equal(ReviewBusinessScope.scopeForLabel("报废车辆资料")?.scope, "old_vehicle");
});

test("应用清单后图片筛选与内置规则选出同一批图片", () => {
  // 生产页面上真实存在的图片：类型已知、分区明确。
  const candidates = [
    { index: 0, src: "a", visible: true, businessScope: "old_vehicle", categoryHint: "vehicle_license", hint: "行驶证", naturalWidth: 800, naturalHeight: 600 },
    { index: 1, src: "b", visible: true, businessScope: "old_vehicle", categoryHint: "registration_certificate", hint: "登记证书", naturalWidth: 700, naturalHeight: 900 },
    { index: 2, src: "c", visible: true, businessScope: "old_vehicle", categoryHint: "scrap_certificate", hint: "报废证明", naturalWidth: 600, naturalHeight: 400 },
    { index: 3, src: "d", visible: true, businessScope: "new_vehicle", categoryHint: "invoice", hint: "发票", naturalWidth: 900, naturalHeight: 500 },
    { index: 4, src: "e", visible: true, businessScope: "new_vehicle", categoryHint: "vehicle_license", hint: "行驶证", naturalWidth: 800, naturalHeight: 600 },
    { index: 5, src: "f", visible: true, businessScope: "business_license", categoryHint: "business_license", hint: "营业执照", naturalWidth: 500, naturalHeight: 700 },
    { index: 6, src: "g", visible: true, businessScope: "other", categoryHint: "unknown", hint: "公司logo", naturalWidth: 100, naturalHeight: 100 },
    { index: 7, src: "h", visible: true, businessScope: "other", categoryHint: "unknown", hint: "暂无图片", emptySlot: true, naturalWidth: 0, naturalHeight: 0 },
  ];

  const beforeManifest = ReviewImageCandidates.select(candidates);
  assert.equal(applyCollectManifest(manifest), true);
  const afterManifest = ReviewImageCandidates.select(candidates);

  const sorted = (result) => result.selected.map((item) => item.index).sort((a, b) => a - b);
  assert.deepEqual(sorted(afterManifest), sorted(beforeManifest));
  assert.equal(afterManifest.eligibleCount, beforeManifest.eligibleCount);
  // 非材料图片（logo、空槽位）两边都不选。
  assert.equal(afterManifest.selected.some((item) => item.index >= 6), false);
});

test("清单补齐了内置类型表漏掉的行驶证，排序因此更准确", () => {
  // 内置 knownTypes 里有登记证、回收证明，却漏了 vehicle_license；
  // 清单里的材料类型表是完整的，所以“行驶证”不再只靠关键词命中。
  applyCollectManifest(null);
  const builtin = ReviewImageCandidates.select([
    { index: 0, src: "a", visible: true, businessScope: "old_vehicle", categoryHint: "vehicle_license", hint: "行驶证", naturalWidth: 800, naturalHeight: 600 },
    { index: 1, src: "b", visible: true, businessScope: "old_vehicle", categoryHint: "scrap_certificate", hint: "报废证明", naturalWidth: 800, naturalHeight: 600 },
  ]);

  assert.equal(applyCollectManifest(manifest), true);
  const fromManifest = ReviewImageCandidates.select([
    { index: 0, src: "a", visible: true, businessScope: "old_vehicle", categoryHint: "vehicle_license", hint: "行驶证", naturalWidth: 800, naturalHeight: 600 },
    { index: 1, src: "b", visible: true, businessScope: "old_vehicle", categoryHint: "scrap_certificate", hint: "报废证明", naturalWidth: 800, naturalHeight: 600 },
  ]);

  // 内置规则里行驶证排在后；清单规则把两者都认作已知类型。
  assert.deepEqual(builtin.selected.map((item) => item.index), [1, 0]);
  assert.deepEqual(fromManifest.selected.map((item) => item.index), [0, 1]);
});

test("清单里的材料关键词让本业务的图片排在前面", () => {
  // 候选不可按类型或分区区分时，材料关键词是唯一的排序依据。
  // 内置正则没有“行驶证”，清单里有；命中关键词的图应当被提到前面。
  const candidates = [
    { index: 0, src: "a", visible: true, businessScope: "unknown", categoryHint: "unknown", hint: "其他", naturalWidth: 300, naturalHeight: 300 },
    { index: 1, src: "b", visible: true, businessScope: "unknown", categoryHint: "unknown", hint: "行驶证", naturalWidth: 300, naturalHeight: 300 },
  ];

  applyCollectManifest(null);
  // 内置规则里两张图分数相同，只能按下标顺序。
  assert.deepEqual(
    ReviewImageCandidates.select(candidates).selected.map((item) => item.index),
    [0, 1],
  );

  assert.equal(applyCollectManifest(manifest), true);
  // 命中材料关键词的排到前面。
  assert.deepEqual(
    ReviewImageCandidates.select(candidates).selected.map((item) => item.index),
    [1, 0],
  );
});

test("小尺寸的材料图不再被面积兜底丢弃", () => {
  // 面积兜底只针对「类型未知且未命中材料关键词」的图。命中关键词的小图
  // （例如尺寸很小的车辆铭牌）必须保留，否则它连重读的机会都没有。
  const tiny = (hint) => [
    { index: 0, src: "a", visible: true, businessScope: "unknown", categoryHint: "unknown", hint, naturalWidth: 100, naturalHeight: 100 },
  ];
  const largeUnknown = [
    { index: 0, src: "a", visible: true, businessScope: "unknown", categoryHint: "unknown", hint: "无关内容", naturalWidth: 300, naturalHeight: 300 },
  ];

  for (const apply of [null, manifest]) {
    applyCollectManifest(apply);
    assert.deepEqual(
      ReviewImageCandidates.select(tiny("登记证书")).selected.map((item) => item.index),
      [0],
      "小尺寸材料图应入选",
    );
    assert.equal(ReviewImageCandidates.select(tiny("无关内容")).eligibleCount, 0, "小尺寸无关图应丢弃");
    assert.equal(ReviewImageCandidates.select(largeUnknown).eligibleCount, 1, "大图应放行");
  }

  // 内置关键词表里没有「行驶证」，清单里有：应用清单后内置表认不出的
  // 小图同样入选，说明关键词判断不再被面积兜底否决。
  applyCollectManifest(null);
  assert.equal(ReviewImageCandidates.select(tiny("行驶证")).eligibleCount, 0);
  assert.equal(applyCollectManifest(manifest), true);
  assert.deepEqual(
    ReviewImageCandidates.select(tiny("行驶证")).selected.map((item) => item.index),
    [0],
  );
});

test("清单里的上传槽位被前端用于材料类型兜底", () => {
  const slots = new Map(
    manifest.materials.flatMap((item) =>
      (item.slots ?? []).map(([scope, order]) => [`${scope}:${order}`, item.document_type]),
    ),
  );

  // 与内置表一致：旧车 1/2/3 分别是行驶证、登记证、回收证明；新车 3 是发票。
  assert.equal(slots.get("old_vehicle:1"), "vehicle_license");
  assert.equal(slots.get("old_vehicle:2"), "registration_certificate");
  assert.equal(slots.get("old_vehicle:3"), "scrap_certificate");
  assert.equal(slots.get("new_vehicle:3"), "invoice");
});

test("面板的中文字段名和材料名来自清单而不是内置表", () => {
  // 清单不可用时退回内置表。
  applyCollectManifest(null);
  assert.equal(fieldLabel("old_vehicle.vin"), "报废车辆车架号");
  assert.equal(applyCollectManifest(manifest), true);

  // 清单里的每个字段标签和材料名都能显示出来，新增业务时面板不用再改表。
  for (const field of manifest.fields) {
    assert.equal(fieldLabel(field.key), field.label, field.key);
  }
  for (const material of manifest.materials) {
    assert.equal(manifestMaterialLabels()[material.document_type], material.label);
  }
  assert.equal(manifestMaterialLabels().invoice, "机动车销售发票");
});

test("应用清单后槽位表来自清单而不是内置表", () => {
  // 清单不可用时退回内置表：已知槽位仍能解析。
  applyCollectManifest(null);
  assert.equal(ReviewBusinessScope.documentTypeFor("old_vehicle", 2, "unknown"), "registration_certificate");

  assert.equal(applyCollectManifest(manifest), true);
  assert.equal(ReviewBusinessScope.documentTypeFor("old_vehicle", 2, "unknown"), "registration_certificate");
  // 清单里的槽位在两边是同一份数据，解析结果必须一致。
  const fromManifest = new Map(manifest.materials.flatMap((item) => (item.slots ?? []).map(([s, o]) => [`${s}:${o}`, item.document_type])));
  for (const [key, expected] of fromManifest) {
    const [scope, order] = key.split(":");
    assert.equal(ReviewBusinessScope.documentTypeFor(scope, Number(order), "unknown"), expected, key);
  }
});

test("分区名不参与材料定类型，报废置换的判定结果与内置表一致", () => {
  applyCollectManifest(manifest);

  // 「旧车资料」是分区标题，同时声明给了回收证明和行驶证。拿它定类型会让一张只
  // 写着分区名的图从 old_vehicle 漂移成 scrap_certificate——那是行为变更不是修复。
  // 排除分组名之后它落回内置表，判定与改动前完全相同。
  assert.equal(manifestHintType("旧车资料"), null);
  assert.equal(manifestHintType("新车资料"), null);
  assert.equal(manifestHintType("报废车辆资料"), null);
  assert.equal(manifestHintType("身份证正面"), null);
  assert.equal(manifestHintType("营业执照"), null);
  // 材料自己的名字照常命中，且与内置表的判定结果一致。
  assert.equal(manifestHintType("行驶证"), "vehicle_license");
  assert.equal(manifestHintType("登记证书"), "registration_certificate");
  assert.equal(manifestHintType("回收证明"), "scrap_certificate");
  assert.equal(manifestHintType("机动车登记证"), "registration_certificate");
});

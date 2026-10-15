/**
 * 车源审核的采集清单：后端声明 → 前端查找路径的端到端一致性。
 *
 * fixture 由后端 `build_collect_manifest(VEHICLE_SOURCE_PACK)` 生成，与
 * `tests/fixtures/collect-manifest.json`（报废置换）同一来源。这组用例走前端
 * 真实的查找路径，验证车源页面的图片能落到 `vehicle` 分区、13 个字段别名
 * 不互相冲突、材料关键词能筛出审核材料。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  applyCollectManifest,
  manifestGroupLabelEntries,
  manifestIdentityAnchors,
  manifestImageProfile,
  manifestMaterialLabels,
  manifestSlotEntries,
  manifestWritableControlKinds,
  manifestWritableFields,
} from "../src/platform/browser/catalog/collect-manifest.ts";
import { buildPageFingerprint } from "../src/platform/browser/identity/page-identity.ts";
import { ReviewBusinessScope } from "../src/platform/browser/collection/business-scope.ts";
import { ReviewImageCandidates } from "../src/platform/browser/collection/image-candidates.ts";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import { businessLabels } from "../src/features/review/model/review-panel.ts";
import { loadSource } from "./tsx-loader.mjs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const { resolveWorkbenchRenderer } = loadSource("features/review/ui/workbench-renderers.tsx");

/** 构造一个审核任务；只填本组用例关心的字段。 */
const step = (step_id, sequence, category, label, reason, result_status) => ({
  step_id,
  sequence,
  category,
  display_target: "ASSISTANT",
  requires_reviewer_action: result_status !== "MATCH",
  label,
  result_status,
  reason,
  page_value: null,
  page_values: [],
  values: [],
  evidence: [],
  check_ids: [step_id],
});

const manifest = JSON.parse(
  readFileSync(new URL("./fixtures/collect-manifest-vehicle-source.json", import.meta.url), "utf8"),
);

test("车源清单声明了唯一的车辆分区和三类材料", () => {
  assert.equal(manifest.business_type, "vehicle_source");
  assert.deepEqual(manifest.scopes, ["vehicle"]);
  assert.deepEqual(
    manifest.materials.map((item) => item.document_type),
    ["vehicle_license", "registration_certificate", "vehicle_nameplate"],
  );
});

test("页面区块标题解析到车辆分区，图片才能被路由", () => {
  assert.equal(applyCollectManifest(manifest), true);

  for (const group of manifest.page_groups) {
    assert.deepEqual(
      { ...ReviewBusinessScope.scopeForLabel(group.label) },
      { scope: group.scope, title: group.title },
      group.label,
    );
  }

  const assigned = ReviewBusinessScope.assign([
    { kind: "label", text: "行驶证信息" },
    { kind: "image", index: 1 },
    { kind: "label", text: "车辆铭牌" },
    { kind: "image", index: 2 },
  ]);
  assert.deepEqual(
    Array.from(assigned, (item) => ({ scope: item.businessScope, order: item.groupOrder })),
    [{ scope: "vehicle", order: 1 }, { scope: "vehicle", order: 2 }],
  );
});

test("未声明的区块标题不会把图片错归到车辆分区", () => {
  applyCollectManifest(manifest);

  assert.equal(ReviewBusinessScope.scopeForLabel("结算信息"), null);
  assert.deepEqual(
    Array.from(ReviewBusinessScope.assign([{ kind: "image", index: 1 }])),
    [{ index: 1, businessScope: "unknown", groupTitle: "未分类资料", groupOrder: 1, imageId: "unknown-01" }],
  );
});

test("车源不声明上传槽位，图片类型交给页面文案和模型识别", () => {
  applyCollectManifest(manifest);

  assert.deepEqual(manifest.materials.flatMap((item) => item.slots ?? []), []);
  assert.deepEqual(manifestSlotEntries(), []);
  // 清单生效时内置槽位表不再参与，避免报废置换的槽位串到车源页面。
  assert.equal(ReviewBusinessScope.documentTypeFor("vehicle", 1, "unknown"), "unknown");
  assert.equal(ReviewBusinessScope.documentTypeFor("vehicle", 1, "vehicle_nameplate"), "vehicle_nameplate");
  assert.equal(ReviewBusinessScope.documentTypeFor("vehicle", 1, "vehicle_license"), "vehicle_license");
});

test("13 个字段别名与页面分区在前端解析一致", () => {
  applyCollectManifest(manifest);

  const aliases = new Set();
  for (const field of manifest.fields) {
    assert.equal(field.aliases[0], field.label, field.key);
    for (const alias of field.aliases) {
      assert.equal(aliases.has(alias), false, `别名重复：${alias}`);
      aliases.add(alias);
      assert.equal(ReviewPageFieldCollector.definitionForLabel(alias)?.field, field.key, alias);
      assert.equal(ReviewPageFieldCollector.definitionForLabel(alias)?.section, field.section, alias);
    }
  }
  assert.equal(manifest.fields.length, 13);
});

test("材料关键词能筛出车源审核材料并给出显示名", () => {
  applyCollectManifest(manifest);

  const profile = manifestImageProfile();
  for (const material of manifest.materials) {
    for (const hint of material.hints) {
      assert.equal(profile.hintsPattern.test(hint), true, hint);
    }
  }
  assert.equal(profile.hintsPattern.test("结算信息"), false);
  assert.equal(manifestMaterialLabels().vehicle_nameplate, "车辆铭牌");
  assert.equal(manifestMaterialLabels().vehicle_license, "机动车行驶证");
  assert.equal(manifestGroupLabelEntries().length, manifest.page_groups.length);
});

test("指纹锚点必须由业务声明，否则页面写回和原图定位全都会被拒", () => {
  applyCollectManifest(manifest);

  assert.deepEqual(manifestIdentityAnchors(), ["vehicle.vin", "vehicle.plate_no", "vehicle.owner"]);
  const fields = { "vehicle.vin": "LGAG4DY36J8019547", "vehicle.plate_no": "鲁B12345" };
  assert.notEqual(buildPageFingerprint(fields, manifestIdentityAnchors()), "");
  // 内置兜底表是报废置换的字段名；车源页面上一个都没有，指纹会退化成空串。
  assert.equal(buildPageFingerprint(fields), "");

  // 强锚点（车架号）一个都没有时宁可返回空指纹，也不拿弱字段充当身份。
  assert.equal(buildPageFingerprint({ "vehicle.owner": "某某物流有限公司" }, ["vehicle.owner"]), "");
});

test("写回白名单由清单下发：13 个字段及已支持的控件类型", () => {
  applyCollectManifest(manifest);

  const writable = manifestWritableFields();
  assert.deepEqual([...writable].sort(), manifest.fields.map((field) => field.key).sort());
  assert.deepEqual([...manifestWritableControlKinds()].sort(), ["date", "number", "select", "text", "textarea"]);

  // 清单不可用时退回内置表，而不是"什么都不允许"。
  applyCollectManifest(null);
  assert.equal(manifestWritableFields(), null);
  assert.equal(manifestWritableControlKinds(), null);
});

test("只有证件照分组的图片进入上传候选", () => {
  applyCollectManifest(manifest);

  // 真实页面的图片分布：证件照（4 张）＋ 车况承诺书（1 张）＋ 车况照片（2 张）。
  const candidates = [
    { index: 1, src: "a.jpg", visible: true, businessScope: "vehicle", categoryHint: "vehicle_license", hint: "行驶证反面", groupTitle: "证件照", naturalWidth: 800, naturalHeight: 600 },
    { index: 2, src: "b.jpg", visible: true, businessScope: "vehicle", categoryHint: "registration_certificate", hint: "登记证书1、2页", groupTitle: "证件照", naturalWidth: 800, naturalHeight: 600 },
    { index: 3, src: "c.jpg", visible: true, businessScope: "vehicle", categoryHint: "registration_certificate", hint: "登记证书3、4页", groupTitle: "证件照", naturalWidth: 800, naturalHeight: 600 },
    { index: 4, src: "d.jpg", visible: true, businessScope: "vehicle", categoryHint: "vehicle_license", hint: "行驶证正面", groupTitle: "证件照", naturalWidth: 800, naturalHeight: 600 },
    { index: 5, src: "e.jpg", visible: true, businessScope: "other", categoryHint: "unknown", hint: "车况承诺书", groupTitle: "车况承诺书", naturalWidth: 900, naturalHeight: 1200 },
    { index: 6, src: "f.jpg", visible: true, businessScope: "other", categoryHint: "unknown", hint: "左前45度", groupTitle: "车况照片", naturalWidth: 1600, naturalHeight: 1200 },
    { index: 7, src: "g.jpg", visible: true, businessScope: "other", categoryHint: "unknown", hint: "右前45度", groupTitle: "车况照片", naturalWidth: 1600, naturalHeight: 1200 },
  ];

  const { selected } = ReviewImageCandidates.select(candidates);

  // 车况照片面积很大，不能因为面积兜底被当成审核材料上传。
  assert.deepEqual(selected.map((item) => item.index).sort((a, b) => a - b), [1, 2, 3, 4]);
});

test("车源审核使用字段优先工作台并显示材料要求", () => {
  assert.equal(businessLabels.vehicle_source, "车源审核");

  const renderer = resolveWorkbenchRenderer({
    business_type: "vehicle_source",
    region: "default",
    profile_version: "1.0",
    review_tasks: [{ step_id: "FIELD-vehicle.vin", sequence: 1, category: "FIELD", label: "VIN", result_status: "MATCH", reason: "一致" }],
  });
  assert.notEqual(renderer, null);

  // 配置版本升级仍使用同一个完整工作台，不退化为简化任务清单。
  assert.equal(
    resolveWorkbenchRenderer({
      business_type: "vehicle_source",
      region: "default",
      profile_version: "9.9",
      review_tasks: [],
    }),
    renderer,
  );
});

test("车源工作台保留字段冲突且不展示旧材料任务", () => {
  const workbench = loadSource("features/review/ui/EvidenceReviewWorkbench.tsx").EvidenceReviewWorkbench;
  const review = {
    business_type: "vehicle_source",
    region: "default",
    profile_version: "1.0",
    qr_checks: [],
    page_fill_intent: [],
    review_tasks: [
      step("MATERIAL-GROUP", 1, "MATERIAL", "材料完整性", "缺少机动车行驶证", "INSUFFICIENT"),
      {
        ...step("FIELD-vehicle.model", 2, "FIELD", "车型", "页面车型马力 480 与材料 488 不一致", "CONFLICT"),
        details: { check_ids: ["VEHICLE-MODEL-POWER"], reason_distributed: true },
      },
    ],
  };
  const html = renderToStaticMarkup(React.createElement(workbench, {
    review,
    pageData: { pageFields: {}, images: [] },
  }));

  assert.match(html, /待处理 \(1\)/);
  assert.match(html, /全部字段 \(1\)/);
  assert.doesNotMatch(html, /材料完整性|缺少机动车行驶证/);
  assert.match(html, /页面外核验 \(0\)/);
  assert.match(html, /车型/);
});

test("车型字段行把页面侧和材料侧一一对应地摆出来", () => {
  const workbench = loadSource("features/review/ui/EvidenceReviewWorkbench.tsx").EvidenceReviewWorkbench;
  const model = {
    ...step("FIELD-vehicle.model", 1, "FIELD", "车型", "页面车型排放标准 国六 与材料 国五 不一致", "CONFLICT"),
    details: { check_ids: ["VEHICLE-MODEL-POWER"], reason_distributed: true },
  };
  const html = renderToStaticMarkup(React.createElement(workbench, {
    review: {
      business_type: "vehicle_source",
      region: "default",
      profile_version: "1.0",
      qr_checks: [],
      page_fill_intent: [],
      review_tasks: [{ ...model, page_values: [
        { source: "车型原文", value: "一汽解放新J6P重卡质惠版480马力6X4 LNG牵引车(CA4250P66M25T1A1E6)(国六)" },
        { source: "车型马力", value: "480 马力" },
      ], values: [
        {
          source: "车型马力",
          value: "473 马力（由发动机功率推导）",
          conflicting: true,
          check_reason: "页面车型马力 430 与材料推导值 473、480 不一致",
        },
        {
          source: "车型排放标准",
          value: "国六（由发动机型号的排放后缀推导）",
          conflicting: false,
          check_reason: "页面排放标准 国六 与材料推导值（由发动机型号的排放后缀推导）国六 一致",
        },
      ] }],
    },
    pageData: { pageFields: {}, images: [] },
    externalView: false,
  }));

  // 材料值按字符切分做差异高亮，比对前先去掉标签。
  const text = html.replace(/<[^>]*>/g, "");

  assert.match(text, /车型原文/);
  assert.match(text, /一汽解放新J6P重卡质惠版480马力6X4/);
  // 页面侧和材料侧都带标签，审核员能看出每一条比的是什么。
  assert.match(text, /车型马力480 马力/);
  // 字段理由里的那句话说进了它自己的候选框，不用在框和底部理由之间来回对照。
  assert.match(text, /473 马力（由发动机功率推导）车型马力 · 与页面不一致页面车型马力 430 与材料推导值 473、480 不一致/);
  assert.match(text, /车型排放标准 · 与页面一致页面排放标准 国六 与材料推导值（由发动机型号的排放后缀推导）国六 一致/);

  // 理由已经逐条搬进候选框，不再重复整段；「不一致」三个字标红，「一致」不标。
  assert.doesNotMatch(text, /；页面整车型号/);
  assert.equal((html.match(/<mark class="mismatch">不一致<\/mark>/g) ?? []).length, 2);
  assert.doesNotMatch(html, /<mark class="mismatch">一致<\/mark>/);
});

test("没有投影的字段不会凭空多出一句比对说明", () => {
  const workbench = loadSource("features/review/ui/EvidenceReviewWorkbench.tsx").EvidenceReviewWorkbench;
  const html = renderToStaticMarkup(React.createElement(workbench, {
    review: {
      business_type: "vehicle_source",
      region: "default",
      profile_version: "1.0",
      qr_checks: [],
      page_fill_intent: [],
      review_tasks: [{
        ...step("FIELD-vehicle.vin", 1, "FIELD", "VIN", "页面与图片不一致", "CONFLICT"),
        values: [{ source: "图片识别", value: "LFNAHUKP1H1E12345" }],
      }],
    },
    pageData: { pageFields: {}, images: [] },
    externalView: false,
  }));

  const text = html.replace(/<[^>]*>/g, "");
  assert.match(text, /LFNAHUKP1H1E12345/);
  assert.doesNotMatch(text, /与页面一致|与页面不一致/);
  // 理由没有搬进候选框，整段理由照旧显示，不能把唯一的说明也藏掉。
  assert.match(text, /页面与图片不一致/);
});

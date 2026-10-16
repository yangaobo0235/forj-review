/**
 * 过户审核的采集清单：后端声明 → 前端查找路径的端到端一致性。
 *
 * fixture 由后端 `build_collect_manifest(TRANSFER_PACK)` 生成。这组用例锁两件
 * 容易静默失效的事：
 *
 * 1. **「车源发布时间」在页面上是纯文本，不是输入框。** 采集器读标签 + 后继
 *    文本的那条路径（`structuredCandidates` / `adjacentCandidates`）**只在标签
 *    已被声明时才工作**（`isKnownLabel`）。清单里漏掉这个别名，采集器就一个值
 *    都拿不到，而开票日期那条规则只会报「待复核」——不报错，只是永远不生效。
 * 2. 共用地址的两个业务特征文案不能撞车，撞了就互相误判。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  applyCollectManifest,
  manifestHintType,
  manifestIdentityAnchors,
  manifestWritableControlKinds,
  manifestWritableFields,
} from "../src/platform/browser/catalog/collect-manifest.ts";
import { ReviewBusinessScope } from "../src/platform/browser/collection/business-scope.ts";
import { ReviewPageFieldCollector } from "../src/platform/browser/collection/page-field-collector.ts";
import { businessLabels } from "../src/features/review/model/review-panel.ts";
import { loadSource } from "./vue-loader.mjs";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";

const { resolveWorkbenchRenderer } = loadSource("features/review/ui/workbench-renderers.ts");

const manifest = JSON.parse(
  readFileSync(new URL("./fixtures/collect-manifest-transfer.json", import.meta.url), "utf8"),
);
const pageCatalog = JSON.parse(
  readFileSync(new URL("./fixtures/page-catalog.json", import.meta.url), "utf8"),
);

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
  details: {},
});

test("过户清单声明了一个分区和两类材料", async () => {
  assert.equal(manifest.business_type, "transfer");
  assert.deepEqual(manifest.scopes, ["transfer"]);
  assert.deepEqual(
    manifest.materials.map((item) => item.document_type),
    ["registration_certificate", "used_car_invoice"],
  );
  // 登记证书按图片页脚印刷的页码统计，缺第 3、4 页要能报出来。
  assert.equal(manifest.materials[0].label, "机动车登记证书");
});

test("14 个核对字段的别名在前端解析唯一", async () => {
  assert.equal(applyCollectManifest(manifest), true);

  const aliases = new Set();
  for (const field of manifest.fields) {
    assert.equal(field.aliases[0], field.label, field.key);
    for (const alias of field.aliases) {
      assert.equal(aliases.has(alias), false, `别名重复：${alias}`);
      aliases.add(alias);
      assert.equal(ReviewPageFieldCollector.definitionForLabel(alias)?.field, field.key, alias);
    }
  }
  // 14 个核对字段 + 车源发布时间（只读展示）。
  assert.equal(manifest.fields.length, 15);
});

test("车源发布时间是纯文本，但必须被声明成已知标签才能采到", async () => {
  applyCollectManifest(manifest);

  // 采集器读「标签 + 后继文本」的那条路径以 isKnownLabel 为闸门。这条断言
  // 就是那个闸门：别名没进清单，值永远采不到，而且不报错。
  assert.equal(
    ReviewPageFieldCollector.definitionForLabel("车源发布时间")?.field,
    "application.source_published_at",
  );
  // 带中文冒号的写法也要认——页面上标签就是这么写的。
  assert.equal(
    ReviewPageFieldCollector.definitionForLabel("车源发布时间：")?.field,
    "application.source_published_at",
  );
  assert.equal(
    manifest.fields.find((field) => field.key === "application.source_published_at").reviewable,
    false,
  );
});

test("过户资料归到过户分区，身份证和营业执照不上传", async () => {
  applyCollectManifest(manifest);

  assert.deepEqual(
    { ...ReviewBusinessScope.scopeForLabel("过户资料") },
    { scope: "transfer", title: "过户资料" },
  );
  // 业务口径已确认：过户只审登记证书和二手车发票，身份证与营业执照不参审，
  // 因此归到 other，在选图阶段就被淘汰、不采集不识别。
  for (const label of ["身份证正面", "身份证反面", "营业执照", "其他图片"]) {
    assert.notEqual(ReviewBusinessScope.scopeForLabel(label)?.scope, "transfer", label);
  }
});

test("材料类型优先按清单声明的关键词判定，不借用别的业务的类型", async () => {
  applyCollectManifest(manifest);

  // 页面小标题「二手车发票」在浏览器内置关键词表里只会命中宽泛的"发票"，
  // 判成报废置换的机动车销售发票，字段白名单跟着错。清单声明了归属，就必须先问清单。
  assert.equal(manifestHintType("二手车发票"), "used_car_invoice");
  assert.equal(manifestHintType("二手车销售统一发票"), "used_car_invoice");
  assert.equal(manifestHintType("登记证书1、2页"), "registration_certificate");
  // 整段容器文案里宽泛词和精确词同时出现时，最具体的那个赢。
  assert.equal(manifestHintType("发票 二手车发票"), "used_car_invoice");
  // 本业务没声明的材料不能凭空判出类型，交回内置表和后端分类处理。
  assert.equal(manifestHintType("车身颜色"), null);
  // 分区名不参与定类型：「过户资料」是分组标题，不是某一份材料。
  assert.equal(manifestHintType("过户资料"), null);
});

test("写回支持文本、下拉和日期，指纹用强锚点", async () => {
  applyCollectManifest(manifest);

  assert.equal(manifestWritableFields()?.has("transfer.plate_no"), true);
  // 开票日期通过日历适配器回填，字段和控件类型必须同时获准。
  assert.equal(manifestWritableFields()?.has("transfer.invoice_date"), true);
  assert.deepEqual([...manifestWritableControlKinds()], ["text", "textarea", "number", "select", "date"]);

  const anchors = manifestIdentityAnchors();
  assert.equal(anchors.includes("transfer.vin"), true);
  // 证件号是个人隐私，不放进指纹。
  assert.equal(anchors.includes("transfer.buyer_id"), false);
});

test("共用地址的三个审核按明确页签名称区分", async () => {
  const shared = pageCatalog.identities.filter((item) =>
    item.paths.includes("/consistency-qingdao"));

  assert.deepEqual(shared.map((item) => item.business_type).sort(), ["consistency", "transfer", "vehicle_source"]);
  assert.deepEqual(Object.fromEntries(shared.map(item => [item.business_type, item.review_tab])), {
    vehicle_source: "车源审核", transfer: "过户审核", consistency: "一致性审核",
  });
  assert.equal(shared.some(item => ["车源详情", "过户详情"].includes(item.review_tab)), false);
});

test("过户审核使用完整证据工作台且不展示旧材料任务", async () => {
  const renderer = resolveWorkbenchRenderer({
    business_type: "transfer",
    region: "default",
    profile_version: "1.0",
    review_tasks: [step("FIELD-transfer.plate_no", 1, "FIELD", "车牌号", "一致", "MATCH")],
  });

  assert.ok(renderer, "过户审核必须有自己的渲染器注册");
  const markup = await renderToString(
    renderer({
      review: {
        business_type: "transfer",
        region: "default",
        profile_version: "1.0",
        recommendation: "PASS",
        summary: "",
        comparisons: [],
        qr_checks: [],
        cross_checks: [],
        issues: [],
        sections: [],
        review_tasks: [
          step("FIELD-transfer.plate_no", 1, "FIELD", "车牌号", "字段一致", "MATCH"),
          // 旧后端响应的材料任务不再展示或参与统计。
          step("MATERIAL-GROUP", 2, "MATERIAL", "材料完整性", "缺少登记证书第 3、4 页", "INSUFFICIENT"),
        ],
      },
      pageData: null,
      onFocusImage: async () => ({ ok: true }),
      onApplyPageFieldValue: async () => ({ ok: true }),
      onApplyPageFieldGroupValue: async () => ({ ok: true }),
      onRerun: async () => {},
    }),
  );

  assert.doesNotMatch(markup, /缺少登记证书|材料完整性/);
  assert.match(markup, /待处理 \(0\)/);
  assert.match(markup, /自动通过 1/);
  assert.match(markup, /页面外核验 \(0\)/);
  assert.match(markup, /全部字段 \(1\)/);
});

test("过户审核不再是「未配置」，下拉里能手动选", async () => {
  assert.equal(businessLabels.transfer, "过户审核");
});

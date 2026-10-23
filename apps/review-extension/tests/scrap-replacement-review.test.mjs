import ReviewResults from "../src/features/review/ui/ReviewResults.vue";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";

/** 构造一个审核任务；页面字段由 step_id 前缀推导。 */
const makeStep = (overrides) => ({
  sequence: 1,
  category: "BUSINESS_RULE",
  display_target: "ASSISTANT",
  requires_reviewer_action: false,
  label: "审核步骤",
  result_status: "MATCH",
  reason: "",
  values: [],
  evidence: [],
  ...overrides,
  page_field:
    overrides.page_field ??
    (String(overrides.step_id || "").startsWith("FIELD-")
      ? String(overrides.step_id).slice(6)
      : null),
});

function workbenchReview(reviewSteps, overrides = {}) {
  return {
    business_type: "scrap_replacement",
    region: "changchun",
    profile_version: "1.0",
    review_tasks: reviewSteps,
    qr_checks: [],
    material_completeness: { phase: "EXTRACTED", status: "COMPLETE", enforced: false, issues: [] },
    ...overrides,
  };
}

test("all profiles keep exactly three views and ignore complete, missing and uncertain legacy material tasks", async () => {
  for (const [business_type, region] of [
    ["scrap_replacement", "qingdao"],
    ["scrap_replacement", "changchun"],
    ["vehicle_source", "default"],
    ["transfer", "default"],
    ["generated_page", "generated_region"],
  ]) {
    for (const result_status of ["MATCH", "INSUFFICIENT"]) {
      const tasks = [
        makeStep({ step_id: "FIELD-vehicle.vin", category: "FIELD", label: "车架号" }),
        ...["MATERIAL-GROUP", "MATERIAL-MISSING_MATERIAL-1", "BUSINESS-MATERIAL-COMPLETENESS"].map(
          (step_id) =>
            makeStep({
              step_id,
              category: step_id.startsWith("BUSINESS-") ? "BUSINESS_RULE" : "MATERIAL",
              visible: true,
              label: "材料完整性",
              result_status,
              requires_reviewer_action: result_status !== "MATCH",
              reason: "材料诊断",
            }),
        ),
        makeStep({
          step_id: "BUSINESS-OWNER-CONSISTENCY-001",
          label: "车辆所有人一致性",
          result_status: "CONFLICT",
          requires_reviewer_action: true,
          reason: "所有人不一致",
        }),
        makeStep({
          step_id: "QR-GROUP",
          category: "EXTERNAL",
          label: "二维码官网核验",
          result_status: "INSUFFICIENT",
          requires_reviewer_action: true,
        }),
      ];
      const html = await renderToString(
        h(ReviewResults, {
          review: workbenchReview(tasks, {
            business_type,
            region,
            material_completeness: {
              status: result_status === "MATCH" ? "COMPLETE" : "UNCERTAIN",
              issues: [],
            },
          }),
          pageData: { pageFields: {}, images: [] },
        }),
      );
      const tabs = html.match(/<nav class="workbench-tabs"[^>]*>([\s\S]*?)<\/nav>/)[1];
      assert.deepEqual(
        [...tabs.matchAll(/<button[^>]*>([^<]+)<\/button>/g)].map((match) => match[1]),
        ["待处理 (2)", "全部字段 (1)", "页面外核验 (2)"],
      );
      assert.match(html, /自动通过 1/);
      assert.match(html, /车辆所有人一致性/);
      assert.match(html, /二维码官网核验/);
      assert.doesNotMatch(html, /材料完整性|材料诊断/);
    }
  }
});

test("missing field tasks report a contract error instead of showing an obsolete result", async () => {
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview(undefined, { recommendation: "PASS", comparisons: [], sections: [] }),
      pageData: null,
    }),
  );
  assert.match(html, /role="alert"/);
  assert.match(html, /审核服务未返回有效的字段任务，请重新审核/);
  assert.doesNotMatch(html, /workbench-tabs|自动通过|回填此值/);
});

test("generated profiles keep page values, source thumbnails and complete rule explanations", async () => {
  const field = "new_page__vehicle.identity";
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview(
        [
          makeStep({
            step_id: `FIELD-${field}`,
            category: "FIELD",
            label: "车辆识别代号",
            result_status: "CONFLICT",
            requires_reviewer_action: true,
            reason: "登记证书与页面的车辆标识不一致",
            values: [
              {
                source: "机动车登记证书 · 当前车辆",
                value: "MATERIAL-VIN",
                image_id: "registration",
              },
            ],
          }),
        ],
        { business_type: "new_page", region: "new_region", profile_version: "config-generated" },
      ),
      pageData: {
        pageFields: { [field]: "PAGE-VIN" },
        images: [{ imageId: "registration", src: "https://example.test/material.jpg" }],
      },
    }),
  );
  assert.match(html, /页面原始值/);
  assert.match(html, /PAGE-VIN/);
  assert.match(html.replace(/<[^>]*>/g, ""), /MATERIAL-VIN/);
  assert.match(html, /机动车登记证书/);
  assert.match(html, /登记证书与页面的车辆标识不一致/);
  assert.match(html, /src="https:\/\/example.test\/material.jpg"/);
  assert.match(html, /查看原图/);
  assert.match(html, /class="workbench-content"/);
});

test("manual input defaults to the page value below material candidates on every field card", async () => {
  for (const name of [
    "scrap_certificate.certificate_no",
    "application.reference_number",
    "old_vehicle.engine_model",
  ]) {
    const step = makeStep({
      step_id: `FIELD-${name}`,
      category: "FIELD",
      label: "审核字段",
      requires_reviewer_action: true,
      result_status: "CONFLICT",
      values: [{ source: "材料", value: "OCR-WRONG" }],
    });
    const html = await renderToString(
      h(ReviewResults, {
        review: workbenchReview([step]),
        pageData: { pageFields: { [name]: "PAGE-WRONG" }, images: [] },
      }),
    );
    assert.match(html, /<input[^>]*value="PAGE-WRONG"/);
    assert.ok(html.indexOf('class="manual-fill"') > html.indexOf('class="candidate-list"'));
    assert.doesNotMatch(html, /<mark class=""/);
    assert.equal((html.match(/回填此值/g) || []).length, 2);
  }
});

test("emission standard shows one recommendation before evidence and keeps evidence read-only", async () => {
  const field = makeStep({
    step_id: "FIELD-old_vehicle.emission_standard",
    category: "FIELD",
    page_field: "old_vehicle.emission_standard",
    label: "排放标准",
    writable: true,
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    values: [
      { source: "登记证书", value: "BF4M2012-16E4", fill_role: "EVIDENCE" },
      {
        source: "发动机型号规则推导",
        value: "国四",
        fill_role: "RECOMMENDATION",
        derived_from: "E4",
      },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([field]),
      pageData: { pageFields: { "old_vehicle.emission_standard": "国五" }, images: [] },
    }),
  );
  const text = html.replace(/<[^>]*>/g, "");

  assert.ok(text.indexOf("系统推荐结论") < text.indexOf("材料核对值"));
  assert.match(text, /国四/);
  assert.match(html, /aria-label="排放标准回填材料值"/);
  assert.equal((html.match(/<section class="candidate">/g) || []).length, 2);
  assert.equal((html.match(/aria-label="排放标准回填材料值"/g) || []).length, 1);
  const evidenceCard = html.slice(
    html.indexOf('<div class="candidate-list">'),
    html.indexOf('<div class="manual-fill">'),
  );
  assert.doesNotMatch(evidenceCard, /fill-value-button/);
  assert.equal((html.match(/>\s*回填此值\s*<\/button>/g) || []).length, 2); // 推荐结论与独立人工输入各一处。
});

test("workbench shows a merged rule conclusion once, under its field", async () => {
  // 合并是**后端**做的：规则检查项声明了所属字段（`field_check_bindings`），
  // 结论就并进那一行，后端也不再把它单独发出来。前端原样渲染，不自己拿表
  // 去猜该藏哪条——前端持有那张表时，新增一个带日期规则的业务得回来补一行。
  const dateField = makeStep({
    step_id: "FIELD-invoice.invoice_date",
    sequence: 1,
    category: "FIELD",
    page_field: "invoice.invoice_date",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    label: "开票日期",
    reason: "页面与发票日期冲突；开票日期不符合政策范围",
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([dateField]),
      pageData: { pageFields: { "invoice.invoice_date": "2024-06-25" }, images: [] },
    }),
  );

  assert.equal((html.match(/开票日期不符合政策范围/g) || []).length, 1);
  assert.match(html, /开票日期/);
});

test("workbench excludes hidden material failures from pending and external counts", async () => {
  const step = makeStep({
    visible: false,
    step_id: "MATERIAL-GROUP",
    category: "MATERIAL",
    result_status: "INSUFFICIENT",
    requires_reviewer_action: true,
    reason: "报废证明存在无法确认的字段",
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([step], {
        material_completeness: {
          status: "UNCERTAIN",
          issues: [
            {
              field_details: [
                {
                  field: "old_vehicle.vin",
                  field_label: "报废车辆车架号",
                  material_name: "报废证明",
                  value: "LG6?123",
                  image_id: "scrap-1",
                },
                {
                  field: "scrap_certificate.certificate_no",
                  field_label: "报废证明编号",
                  material_name: "报废证明",
                  value: null,
                  image_id: "scrap-2",
                },
              ],
            },
          ],
          checklist: [{ key: "scrap", display_name: "报废证明", status: "PRESENT", image_ids: [] }],
        },
      }),
      pageData: {
        pageFields: {},
        images: [
          { imageId: "scrap-1", src: "scrap-1.jpg" },
          { imageId: "scrap-2", src: "scrap-2.jpg" },
        ],
      },
    }),
  );
  assert.doesNotMatch(html, /报废证明 · 报废车辆车架号/);
  assert.doesNotMatch(html, /LG6\?123/);
  assert.doesNotMatch(html, /未识别到有效值/);
  assert.doesNotMatch(html, /scrap-1.jpg|scrap-2.jpg/);
  assert.equal((html.match(/查看原图/g) || []).length, 0);
  assert.doesNotMatch(html, /资料完整性|材料完整性/);
  assert.match(html, /待处理 \(0\)/);
  assert.match(html, /页面外核验 \(0\)/);
});

test("field and external reviews only expose the manual review completion action", async () => {
  for (const category of ["FIELD", "BUSINESS_RULE"]) {
    const step = makeStep({
      step_id:
        category === "FIELD" ? "FIELD-page_ocr.new_vehicle_vin" : "BUSINESS-POLICY-NEW-ORIGIN",
      category,
      requires_reviewer_action: true,
      result_status: "CONFLICT",
      label: "核验项",
    });
    const html = await renderToString(
      h(ReviewResults, {
        review: workbenchReview([step]),
        pageData: { pageFields: {}, images: [] },
      }),
    );
    assert.match(html, /标记人工复核/);
    assert.doesNotMatch(html, /确认已核对|保留页面值/);
  }
});

test("invoice origin candidates show their matching invoice thumbnail and original image action", async () => {
  for (const reference of [{ image_id: "invoice-img" }, { image_index: 3 }]) {
    const step = makeStep({
      step_id: "BUSINESS-POLICY-NEW-ORIGIN",
      category: "BUSINESS_RULE",
      result_status: "INSUFFICIENT",
      requires_reviewer_action: true,
      label: "新车发票产地",
      values: [{ source: "新车销售发票", value: "长春", ...reference }],
    });
    const html = await renderToString(
      h(ReviewResults, {
        review: workbenchReview([step]),
        pageData: {
          pageFields: {},
          images: [
            { imageId: "unrelated", index: 1, src: "unrelated.jpg" },
            { imageId: "invoice-img", index: 3, src: "invoice.jpg" },
          ],
        },
      }),
    );
    assert.match(html, /长[\s\S]*春/);
    assert.match(html, /新车销售发票/);
    assert.match(html, /src="invoice.jpg"/);
    assert.match(html, /查看原图/);
    assert.doesNotMatch(html, /src="unrelated.jpg"/);
  }
});

test("QR official links remain clickable for manual review when backend access fails", async () => {
  const url = "https://qclt.mofcom.gov.cn/deal/scrap/validdata/test";
  const step = makeStep({
    step_id: "QR-GROUP",
    category: "EXTERNAL",
    result_status: "INSUFFICIENT",
    requires_reviewer_action: true,
  });
  for (const accessible of [false, null]) {
    const html = await renderToString(
      h(ReviewResults, {
        review: workbenchReview([step], {
          qr_checks: [
            {
              url,
              raw_value: url,
              domain_valid: true,
              accessible,
              page_fields: {},
              status: "REVIEW_REQUIRED",
            },
          ],
        }),
        pageData: { pageFields: {}, images: [] },
      }),
    );
    assert.ok(html.includes(`href="${url}"`));
    assert.match(html, /target="_blank"/);
    assert.match(html, /noreferrer noopener/);
    assert.match(html, /后台暂未访问成功/);
    assert.match(html, /可点击网址人工核验/);
    assert.match(html, /待复核/);
  }
  for (const domain_valid of [false, null]) {
    const html = await renderToString(
      h(ReviewResults, {
        review: workbenchReview([step], {
          qr_checks: [
            {
              url,
              raw_value: url,
              domain_valid,
              accessible: false,
              page_fields: {},
              status: "REVIEW_REQUIRED",
            },
          ],
        }),
        pageData: { pageFields: {}, images: [] },
      }),
    );
    assert.ok(!html.includes(`href="${url}"`));
    assert.match(html, /未取得已确认的官网网址/);
  }
});

test("workbench exposes only a backend-verified QR URL as a new-tab link", async () => {
  const qrStep = makeStep({
    step_id: "QR-GROUP",
    sequence: 1,
    category: "EXTERNAL",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    label: "二维码官网核验",
    reason: "官网字段与图片不一致",
  });
  const url = "https://qcar.mofcom.gov.cn/query/1";
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([qrStep], {
        qr_checks: [
          {
            url,
            domain_valid: true,
            accessible: true,
            page_fields: {},
            status: "CONFLICT",
            message: "不一致",
          },
        ],
      }),
      pageData: { pageFields: {}, images: [] },
    }),
  );

  assert.match(html, new RegExp(`href="${url}"`));
  assert.match(html, /target="_blank"/);
  assert.match(html, /noopener/);
});

test("technical QR details keep a verified raw URL clickable for manual review", async () => {
  const url = "https://qclt.mofcom.gov.cn/deal/scrap/validdata/manual";
  const step = makeStep({
    step_id: "QR-GROUP",
    sequence: 1,
    category: "EXTERNAL",
    result_status: "INSUFFICIENT",
    requires_reviewer_action: true,
    label: "二维码官网核验",
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([step], {
        qr_checks: [
          {
            url,
            raw_value: url,
            domain_valid: true,
            accessible: false,
            page_fields: {},
            status: "REVIEW_REQUIRED",
          },
        ],
      }),
      pageData: { pageFields: {}, images: [] },
    }),
  );
  const details = html.match(/<details class="qr-technical-details">([\s\S]*?)<\/details>/)?.[1];
  assert.ok(details);
  assert.match(details, /原始二维码内容/);
  assert.ok(
    details.includes(`<a href="${url}" target="_blank" rel="noreferrer noopener">${url}</a>`),
  );
  assert.match(html, /后台暂未访问成功/);
});

test("workbench keeps page values at the top and shows a material thumbnail beside its image action", async () => {
  const field = makeStep({
    step_id: "FIELD-old_vehicle.type",
    sequence: 1,
    category: "FIELD",
    label: "报废车辆类型",
    page_value: "牵引车",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    values: [
      { source: "申请页面字段", value: "牵引车" },
      {
        source: "图片识别",
        value: "重型半挂牵引车",
        image_id: "vehicle-license",
        image_index: 2,
        document_type: "vehicle_license",
      },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([field]),
      pageData: {
        pageFields: { "old_vehicle.type": "牵引车" },
        images: [{ imageId: "vehicle-license", src: "vehicle-license.jpg", alt: "旧车行驶证" }],
      },
    }),
  );

  assert.match(html, /页面原始值/);
  assert.match(html, /牵引车/);
  assert.match(html.replace(/<[^>]+>/g, ""), /重型半挂牵引车/);
  assert.match(html, /行驶证/);
  assert.match(html, /class="candidate-evidence"/);
  assert.match(html, /src="vehicle-license\.jpg"/);
  assert.match(html, /查看原图/);
  assert.doesNotMatch(html, /vehicle_license/);
  assert.doesNotMatch(html, /申请页面字段/);
});

test("workbench marks differing identifier positions in red", async () => {
  const field = makeStep({
    step_id: "FIELD-new_vehicle.vin",
    sequence: 1,
    category: "FIELD",
    label: "新车车架号",
    page_value: "LFW5RX9L9TAA15882",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    values: [
      {
        source: "图片识别",
        value: "LFW5RX9L9TAA15082",
        image_id: "vin",
        document_type: "vehicle_license",
        differences: [
          { kind: "REPLACE", start: 14, end: 15, page_start: 14, page_end: 15, page_text: "8" },
        ],
      },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([field]),
      pageData: { pageFields: { "new_vehicle.vin": "LFW5RX9L9TAA15882" }, images: [] },
    }),
  );

  assert.match(html, /class="value-diff"/);
  assert.match(html, /<mark class="value-diff"[^>]*>0<\/mark>/);
});

test("old vehicle VIN keeps the page value plain and marks differing material characters", async () => {
  const difference = {
    kind: "REPLACE",
    start: 16,
    end: 17,
    page_start: 16,
    page_end: 17,
    page_text: "5",
  };
  const field = makeStep({
    step_id: "FIELD-old_vehicle.vin",
    sequence: 1,
    category: "FIELD",
    label: "报废车辆车架号",
    page_value: "LFNAFRJM6BAK00025",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    values: [
      { source: "申请页面字段", value: "LFNAFRJM6BAK00025", differences: [] },
      { source: "行驶证", value: "LFNAFRJM6BAK00024", differences: [difference] },
      { source: "机动车登记证书", value: "LFNAFRJM6BAK00024", differences: [difference] },
      { source: "二维码官网字段", value: "LFNAFRJM6BAK00024", differences: [difference] },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([field]),
      pageData: { pageFields: { "old_vehicle.vin": "LFNAFRJM6BAK00025" }, images: [] },
    }),
  );

  assert.doesNotMatch(html, /<mark class="value-diff"[^>]*>5<\/mark>/);
  assert.equal((html.match(/<mark class="value-diff"[^>]*>4<\/mark>/g) ?? []).length, 3);
});

test("composite VIN task uses Chinese page labels and preserves material diff marks", async () => {
  const field = makeStep({
    step_id: "FIELD-NEW-VEHICLE-VIN",
    sequence: 1,
    category: "FIELD",
    label: "新车车架号",
    page_target_field: "new_vehicle.vin",
    page_target_fields: ["new_vehicle.vin", "page_ocr.new_vehicle_vin"],
    page_values: [
      { source: "新车车架号", value: "VIN-PAGE" },
      { source: "OCR新车车架号", value: "VIN-PAGE" },
    ],
    writable: true,
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    values: [
      {
        source: "图片识别",
        value: "VIN-MATERIAL",
        image_id: "vin",
        document_type: "vehicle_license",
        differences: [
          { kind: "REPLACE", start: 4, end: 5, page_start: 4, page_end: 5, page_text: "P" },
        ],
      },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([field]),
      pageData: {
        pageFields: { "new_vehicle.vin": "VIN-PAGE", "page_ocr.new_vehicle_vin": "VIN-PAGE" },
        images: [],
      },
      onApplyPageFieldGroupValue: async () => ({ ok: true, message: "组合字段已同时回填并回读" }),
    }),
  );

  assert.match(html, /新车车架号/);
  assert.match(html, /OCR新车车架号/);
  assert.doesNotMatch(html, /new_vehicle\.vin/);
  assert.doesNotMatch(html, /page_ocr\.new_vehicle_vin/);
  assert.match(html, /class="value-diff"/);
  assert.match(html, /回填此值/);
});

test("composite invoice task uses Chinese page labels and keeps both write targets", async () => {
  const field = makeStep({
    step_id: "FIELD-INVOICE-CODE-NO",
    sequence: 1,
    category: "FIELD",
    label: "发票代码/号码",
    page_target_field: "invoice.code",
    page_target_fields: ["invoice.code", "invoice.invoice_no"],
    page_values: [
      { source: "发票代码", value: "INV-PAGE" },
      { source: "发票号码", value: "INV-PAGE" },
    ],
    writable: true,
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    values: [
      {
        source: "机动车销售发票",
        value: "INV-MATERIAL",
        differences: [
          { kind: "REPLACE", start: 4, end: 5, page_start: 4, page_end: 5, page_text: "P" },
        ],
      },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([field]),
      pageData: {
        pageFields: { "invoice.code": "INV-PAGE", "invoice.invoice_no": "INV-PAGE" },
        images: [],
      },
    }),
  );

  assert.match(html, /发票代码/);
  assert.match(html, /发票号码/);
  assert.doesNotMatch(html, /invoice\.code/);
  assert.doesNotMatch(html, /invoice\.invoice_no/);
  assert.match(html, /class="value-diff"/);
  assert.match(html, /回填此值/);
});

test("workbench labels a page invoice code derived from the invoice digital number", async () => {
  const field = makeStep({
    step_id: "FIELD-invoice.code",
    sequence: 1,
    category: "FIELD",
    label: "发票代码",
    page_value: "2632000000731322946",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    values: [
      {
        source: "图片识别",
        value: "2632000000731322946",
        image_id: "invoice",
        document_type: "invoice",
        derived_from: "invoice.invoice_no",
      },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([field]),
      pageData: { pageFields: { "invoice.code": "2632000000731322946" }, images: [] },
    }),
  );

  assert.match(html, /由发票数电号码适配/);
});

test("workbench reports the actual DOM-derived field count and unknown controls", async () => {
  const known = makeStep({
    step_id: "FIELD-new_vehicle.vin",
    sequence: 1,
    category: "FIELD",
    label: "新车车架号",
  });
  const added = makeStep({
    step_id: "FIELD-DOM-2",
    sequence: 2,
    category: "FIELD",
    label: "页面新增字段",
    page_value: "新增值",
    result_status: "INSUFFICIENT",
    requires_reviewer_action: true,
    reason: "未配置核验来源",
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([known, added]),
      pageData: { pageFields: {}, reviewFields: [], images: [] },
    }),
  );

  assert.match(html, /全部字段 \(2\)/);
  assert.match(html, /全部字段 \(2\)/);
  assert.match(html, /页面新增字段/);
  assert.match(html, /页面原始值/);
  assert.match(html, /新增值/);
});

test("workbench excludes legacy material tasks and reports from every display count", async () => {
  const materialStep = makeStep({
    step_id: "MATERIAL-GROUP",
    sequence: 1,
    category: "MATERIAL",
    result_status: "INSUFFICIENT",
    requires_reviewer_action: true,
    label: "资料完整性",
    reason: "缺少新车行驶证",
  });
  const checklist = [
    ["old_vehicle:vehicle_license", "旧车行驶证", "PRESENT"],
    ["old_vehicle:registration_certificate", "旧车登记证", "PRESENT"],
    ["old_vehicle:scrap_certificate", "报废证明", "PRESENT"],
    ["new_vehicle:vehicle_license", "新车行驶证", "MISSING"],
    ["new_vehicle:registration_certificate", "新车登记证", "PRESENT"],
    ["new_vehicle:invoice", "新车发票", "PRESENT"],
  ].map(([key, display_name, status]) => ({
    key,
    display_name,
    status,
    material_type: key.split(":")[1],
    business_scope: key.split(":")[0],
    required_pages: [],
    present_pages: [],
    missing_pages: [],
    image_ids: [],
    reason: status === "MISSING" ? `缺少${display_name}` : "材料已确认",
  }));
  const fieldStep = makeStep({
    step_id: "FIELD-new_vehicle.vin",
    category: "FIELD",
    label: "新车车架号",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    reason: "车架号不一致",
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([materialStep, fieldStep], {
        material_completeness: {
          phase: "EXTRACTED",
          status: "INCOMPLETE",
          enforced: true,
          issues: [],
          checklist,
        },
      }),
      pageData: { pageFields: {}, images: [] },
    }),
  );

  assert.equal((html.match(/材料已提供/g) ?? []).length, 0);
  assert.doesNotMatch(html, /缺少新车行驶证|资料完整性|材料完整性/);
  assert.match(html, /新车车架号/);
  assert.match(html, /待处理 \(1\)/);
  assert.match(html, /自动通过 0/);
  assert.match(html, /全部字段 \(1\)/);
  assert.match(html, /页面外核验 \(0\)/);
});

test("registered review results show the field workbench without the large recommendation card", async () => {
  for (const [business_type, region] of [
    ["scrap_replacement", "changchun"],
    ["scrap_replacement", "qingdao"],
    ["vehicle_source", "default"],
    ["transfer", "default"],
  ]) {
    const field = makeStep({
      step_id: "FIELD-old_vehicle.vin",
      category: "FIELD",
      label: "车架号",
      result_status: "CONFLICT",
      requires_reviewer_action: true,
      reason: "车架号待核对",
    });
    const html = await renderToString(
      h(ReviewResults, {
        review: workbenchReview([field], {
          business_type,
          region,
          recommendation: "REVIEW_REQUIRED",
          summary: "建议人工复核",
          cross_checks: [],
          comparisons: [],
          sections: [],
          issues: [],
        }),
        pageData: { pageFields: {}, images: [] },
      }),
    );
    assert.doesNotMatch(html, /Agent 最终审核建议|建议人工复核|recommendation-/);
    assert.match(html, /车架号待核对/);
  }
});

test("workbench hides the affiliation subject task and its identity requirements", async () => {
  const subject = makeStep({
    step_id: "BUSINESS-AFFILIATION-SUBJECT-001",
    sequence: 1,
    category: "BUSINESS_RULE",
    result_status: "INSUFFICIENT",
    requires_reviewer_action: true,
    label: "新旧车挂靠主体关系",
    reason: "张三缺少身份证反面",
    details: {
      subject_requirements: [
        {
          party: "SHARED",
          subject_name: "张三",
          subject_type: "PERSONAL",
          document: "identity_card_front",
          status: "PRESENT",
          image_ids: ["id-front"],
          reason: "身份证正面已确认",
        },
        {
          party: "SHARED",
          subject_name: "张三",
          subject_type: "PERSONAL",
          document: "identity_card_back",
          status: "MISSING",
          image_ids: [],
          reason: "张三缺少身份证反面",
        },
      ],
    },
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([{ ...subject, visible: false }]),
      pageData: {
        pageFields: {},
        images: [{ imageId: "id-front", src: "front.jpg", alt: "身份证正面" }],
      },
    }),
  );

  assert.doesNotMatch(html, /新旧车挂靠主体关系|张三|身份证正面|身份证反面|查看原图/);
  assert.match(html, /页面外核验 \(0\)/);
});

test("workbench hides affiliation auxiliary tasks together with the subject task", async () => {
  const subject = makeStep({
    step_id: "BUSINESS-AFFILIATION-SUBJECT-001",
    sequence: 1,
    category: "BUSINESS_RULE",
    label: "新旧车挂靠主体关系",
    reason: "新旧车公司法定名称一致且营业执照已确认",
    values: [{ source: "旧车所有人", value: "甲运输有限公司" }],
    evidence: [
      {
        source: "营业执照",
        image_id: "license",
        field: "business_license.company_name",
        value: "甲运输有限公司",
      },
      {
        source: "旧车登记证",
        image_id: "registration",
        field: "old_vehicle.owner",
        value: "甲运输有限公司",
      },
    ],
    details: {
      subject_requirements: [
        {
          party: "SHARED",
          subject_name: "甲运输有限公司",
          subject_type: "COMPANY",
          document: "business_license",
          status: "PRESENT",
          image_ids: ["license"],
          reason: "营业执照名称已确认",
        },
      ],
    },
  });
  const customer = makeStep({
    step_id: "BUSINESS-AFFILIATION-AUX-CUSTOMER-NAME",
    sequence: 2,
    category: "BUSINESS_RULE",
    result_status: "INSUFFICIENT",
    requires_reviewer_action: true,
    label: "客户名称",
    reason: "页面或材料未取得可比较的明确值",
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([
        { ...subject, visible: false },
        { ...customer, visible: false },
      ]),
      pageData: {
        pageFields: {},
        images: [
          { imageId: "license", src: "license.jpg", alt: "营业执照原图" },
          { imageId: "registration", src: "registration.jpg", alt: "登记证原图" },
        ],
      },
    }),
  );

  assert.doesNotMatch(html, /新旧车挂靠主体关系|客户名称|页面或材料未取得可比较的明确值/);
  assert.doesNotMatch(html, /src="license.jpg"|src="registration.jpg"/);
  assert.match(html, /页面外核验 \(0\)/);
});

test("规则结论自带页面侧取值时，卡片上先摆「页面原始值」再看材料", async () => {
  const step = makeStep({
    step_id: "BUSINESS-OWNER-CONSISTENCY-001",
    label: "车辆所有人一致性",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    reason: "页面上的新旧车所有人不一致：甲公司 / 乙公司，请人工核对",
    page_values: [
      { source: "报废车辆所有人（页面）", value: "甲公司" },
      { source: "新车所有人（页面）", value: "乙公司" },
    ],
    values: [
      {
        source: "图片识别",
        value: "甲公司",
        detail: "旧车行驶证",
        document_type: "vehicle_license",
        image_id: "img-1",
      },
      {
        source: "图片识别",
        value: "乙公司",
        detail: "新车行驶证",
        document_type: "vehicle_license",
        image_id: "img-2",
      },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([step]),
      pageData: { pageFields: {}, images: [] },
    }),
  );
  const text = html.replace(/<[^>]*>/g, "");

  // 先页面原值，后材料核对值，两个区块的顺序不能颠倒。
  assert.ok(text.indexOf("页面原始值") < text.indexOf("材料核对值"));
  assert.match(text, /报废车辆所有人（页面）甲公司/);
  assert.match(text, /新车所有人（页面）乙公司/);
  assert.match(text, /甲公司/);
});

test("页面原始值不一致时在页面区块下面说一句，整段理由不再重复", async () => {
  const step = makeStep({
    step_id: "BUSINESS-OWNER-CONSISTENCY-001",
    label: "车辆所有人一致性",
    result_status: "CONFLICT",
    requires_reviewer_action: true,
    reason: "页面上的新旧车所有人不一致：甲公司 / 乙公司",
    details: { reason_distributed: true, page_value_note: "新旧车页面所有人不一致" },
    page_values: [
      { source: "报废车辆所有人（页面）", value: "南城瑞顺物流有限公司" },
      { source: "新车所有人（页面）", value: "南城珺顺物流有限公司" },
    ],
    values: [
      { source: "旧车行驶证", value: "南城珺顺物流有限公司", conflicting: true, differences: [] },
    ],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([step]),
      pageData: { pageFields: {}, images: [] },
    }),
  );
  const text = html.replace(/<[^>]*>/g, "");

  assert.match(text, /新旧车页面所有人不一致/);
  assert.ok(text.indexOf("新旧车页面所有人不一致") < text.indexOf("材料核对值"));
  // 理由整段不再重复显示。
  assert.doesNotMatch(text, /页面上的新旧车所有人不一致/);
  // 材料侧的来源标签带新旧车前缀，两边才分得清。
  assert.match(text, /旧车行驶证/);
});

test("没有页面侧取值的规则结论不会凭空多出一行「未采集」", async () => {
  const step = makeStep({
    step_id: "BUSINESS-POLICY-NEW-ORIGIN",
    label: "新车发票产地",
    result_status: "INSUFFICIENT",
    requires_reviewer_action: true,
    values: [{ source: "图片识别", value: "青岛市", document_type: "invoice" }],
  });
  const html = await renderToString(
    h(ReviewResults, {
      review: workbenchReview([step]),
      pageData: { pageFields: {}, images: [] },
    }),
  );

  assert.doesNotMatch(html, /页面原始值/);
  assert.match(html.replace(/<[^>]*>/g, ""), /青岛市/);
});

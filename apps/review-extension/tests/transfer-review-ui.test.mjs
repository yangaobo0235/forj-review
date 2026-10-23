import Detail from "../src/features/review/ui/ReviewStepDetail.vue";
import assert from "node:assert/strict";
import { test } from "vitest";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";

for (const [value, writable, expected] of [
  ["", true, "未填写"],
  [null, false, "未采集"],
]) {
  test(`过户字段${expected}时准确展示并保留正确回填权限`, async () => {
    const field = "transfer_invoice.invoice_no";
    const html = await renderToString(
      h(Detail, {
        step: {
          step_id: `FIELD-${field}`,
          category: "FIELD",
          label: "发票号码",
          page_target_field: field,
          page_value: value,
          writable,
          result_status: "INSUFFICIENT",
          reason: "请核对",
          values: [{ source: "二手车发票", value: "00123456789012345678" }],
          evidence: [],
          details: {},
        },
        pageData: { pageFields: value === null ? {} : { [field]: value }, images: [] },
        qrChecks: [],
        disabled: false,
        onFocusImage: async () => {},
        onFill: async () => {},
        onChoose: () => {},
      }),
    );
    assert.match(html, new RegExp(`<strong>${expected}</strong>`));
    const button = html.match(/<button[^>]*class="fill-value-button"[^>]*>/)?.[0];
    assert.ok(button);
    assert.equal(button.includes("disabled"), !writable);
  });
}

test("识别车架号独立比较发票与页面并标红差异字符", async () => {
  const html = await renderToString(
    h(Detail, {
      step: {
        step_id: "FIELD-transfer_invoice.vin",
        category: "FIELD",
        label: "识别车架号",
        page_target_field: "transfer_invoice.vin",
        page_value: "LFV00000000000009",
        writable: true,
        result_status: "CONFLICT",
        reason: "页面值与二手车发票不一致",
        values: [
          {
            source: "图片识别",
            document_type: "used_car_invoice",
            value: "LFV00000000000001",
            comparison_status: "CONFLICT",
            differences: [
              { kind: "REPLACE", start: 16, end: 17, page_start: 16, page_end: 17, page_text: "9" },
            ],
          },
        ],
        evidence: [],
        details: {},
      },
      pageData: { pageFields: { "transfer_invoice.vin": "LFV00000000000009" }, images: [] },
      qrChecks: [],
      disabled: false,
      onFocusImage: async () => {},
      onFill: async () => {},
      onChoose: () => {},
    }),
  );
  const buttons = html.match(/<button[^>]*aria-label="识别车架号回填材料值"[^>]*>/g) ?? [];
  assert.equal(buttons.length, 1);
  assert.ok(!buttons[0].includes("disabled"));
  assert.match(html, /<mark[^>]*class="value-diff"[^>]*>1<\/mark>/);
  assert.doesNotMatch(html, /value-diff-pending|登记证书VIN需人工核对补全/);
});

import assert from "node:assert/strict";
import test from "node:test";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";
import { loadSource } from "./vue-loader.mjs";

const { default: Detail } = loadSource("features/review/ui/ReviewStepDetail.vue");

for (const [value, writable, expected] of [["", true, "未填写"], [null, false, "未采集"]]) {
  test(`过户字段${expected}时准确展示并保留正确回填权限`, async () => {
    const field = "transfer_invoice.invoice_no";
    const html = await renderToString(h(Detail, {
      step: { step_id: `FIELD-${field}`, category: "FIELD", label: "发票号码",
        page_target_field: field, page_value: value, writable, result_status: "INSUFFICIENT",
        reason: "请核对", values: [{ source: "二手车发票", value: "00123456789012345678" }],
        evidence: [], details: {} },
      pageData: { pageFields: value === null ? {} : { [field]: value }, images: [] },
      qrChecks: [], disabled: false,
      onFocusImage: async () => {}, onFill: async () => {}, onChoose: () => {},
    }));
    assert.match(html, new RegExp(`<strong>${expected}</strong>`));
    const button = html.match(/<button[^>]*class="fill-value-button"[^>]*>/)?.[0];
    assert.ok(button);
    assert.equal(button.includes("disabled"), !writable);
  });
}

test("登记证书VIN不完整不隐藏完整发票候选的回填按钮", async () => {
  const html = await renderToString(h(Detail, {
    step: { step_id: "FIELD-transfer_invoice.vin", category: "FIELD", label: "识别车架号",
      page_target_field: "transfer_invoice.vin", page_value: "LFV0000000000001", writable: true,
      result_status: "INSUFFICIENT", reason: "登记证书VIN需人工核对补全",
      values: [
        { source: "二手车发票", value: "LFV00000000000001", comparison_status: "CONFLICT" },
        { source: "登记证书1、2页", value: "LGB42308617", comparison_status: "INSUFFICIENT" },
      ], evidence: [], details: { permitted_fill_values: ["LFV00000000000001"] } },
    pageData: { pageFields: { "transfer_invoice.vin": "LFV0000000000001" }, images: [] },
    qrChecks: [], disabled: false,
    onFocusImage: async () => {}, onFill: async () => {}, onChoose: () => {},
  }));
  const buttons = html.match(/<button[^>]*aria-label="识别车架号回填材料值"[^>]*>/g) ?? [];
  assert.equal(buttons.length, 1);
  assert.ok(!buttons[0].includes("disabled"));
  assert.match(html, /人工输入值/);
  assert.match(html.replace(/<[^>]*>/g, ""), /LGB42308617/);
});

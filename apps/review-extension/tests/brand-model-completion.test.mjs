// @vitest-environment jsdom
import {test} from "vitest";
import assert from "node:assert/strict";
import {mount} from "@vue/test-utils";
import Detail from "../src/features/review/ui/ReviewStepDetail.vue";

function fixture(status = "READY") {
  const value = "欧曼牌BJ4259SNFKB-XJ";
  const blocked = status !== "READY";
  const calls = [];
  const step = {step_id: "FIELD-vehicle.brand_model", category: "FIELD", label: "品牌型号",
    page_target_field: "vehicle.brand_model", result_status: blocked ? "INSUFFICIENT" : "CONFLICT",
    reason: blocked ? "行驶证与辅助材料VIN不一致" : `品牌型号应为${value}，请回填完整值`, writable: true,
    values: [{source: "行驶证", document_type: "vehicle_license", value: "欧曼牌BJ4259SNFKB-X", image_id: "license"},
      {source: "铭牌", document_type: "vehicle_nameplate", value: "BJ4259SNFKB-XJ", image_id: "plate"},
      {source: "完整品牌型号", value, derived_from: "行驶证品牌文字＋完整整车型号"}], evidence: [],
    details: {permitted_fill_values: blocked ? [] : [value], brand_model_completion: {
      license_value: "欧曼牌BJ4259SNFKB-X", brand_text: "欧曼牌", license_model: "BJ4259SNFKB-X",
      auxiliary_model: "BJ4259SNFKB-XJ", completed_value: value, status,
      basis: "行驶证品牌文字＋辅助材料完整整车型号", issues: blocked ? ["行驶证与辅助材料VIN不一致"] : [],
    }},
  };
  const wrapper = mount(Detail, {props: {step, disabled: false, qrChecks: [],
    pageData: {pageFields: {"vehicle.brand_model": "欧曼牌BJ4259SNFKB-X"},
      images: [{imageId: "license", src: "https://example.test/license.jpg"},
        {imageId: "plate", src: "https://example.test/plate.jpg"}]},
    onFill: async (task, text) => { calls.push([task.page_target_field, text]); },
    onFocusImage: async id => { calls.push(["image", id]); }, onChoose: () => {},
  }});
  return {wrapper, calls, value};
}

test("brand card explains completion and fills only the full branded result", async () => {
  const {wrapper, calls, value} = fixture();
  assert.match(wrapper.text(), /合成结论 · 完整品牌型号欧曼牌BJ4259SNFKB-XJ/);
  assert.equal(wrapper.get('.completion-suffix').text(), "J");
  assert.equal(wrapper.find('.candidate-list').exists(), false);
  assert.equal(wrapper.get('.brand-manual').element.tagName, "SECTION");
  assert.equal(wrapper.get('.completion-materials').element.open, true);
  assert.equal(wrapper.findAll('.completion-evidence').length, 2);
  assert.deepEqual(wrapper.findAll('.completion-evidence img').map(image => image.attributes('src')),
    ["https://example.test/license.jpg", "https://example.test/plate.jpg"]);
  assert.match(wrapper.findAll('.completion-evidence')[0].text(), /行驶证 · 品牌型号/);
  assert.match(wrapper.findAll('.completion-evidence')[1].text(), /铭牌 · 整车型号/);
  await wrapper.get('.completion-actions button').trigger('click');
  assert.deepEqual(calls, [["vehicle.brand_model", value]]);
  await wrapper.findAll('.image-action')[0].trigger('click');
  assert.deepEqual(calls.at(-1), ["image", "license"]);
  wrapper.unmount();
});

test("VIN-blocked combination is provisional, explains the disabled action and allows manual entry", async () => {
  const {wrapper, calls} = fixture("BLOCKED");
  assert.match(wrapper.text(), /待核实组合值 · 暂不可回填/);
  assert.equal((wrapper.text().match(/VIN不一致/g) || []).length, 1);
  assert.equal(wrapper.get('.completion-actions button').element.disabled, true);
  assert.match(wrapper.get('.completion-actions button').text(), /材料待核对/);
  await wrapper.get('.completion-actions button').trigger('click');
  assert.deepEqual(calls, []);
  assert.match(wrapper.text(), /人工核对后填写/);
  await wrapper.get('.brand-manual input').setValue("人工确认牌MODEL123");
  await wrapper.get('.brand-manual button').trigger('click');
  assert.deepEqual(calls, [["vehicle.brand_model", "人工确认牌MODEL123"]]);
  assert.equal(wrapper.props('step').result_status, "INSUFFICIENT");
  wrapper.unmount();
});

test("completion presentation cannot authorize a value absent from permitted fill values", async () => {
  const {wrapper} = fixture();
  await wrapper.setProps({step: {...wrapper.props('step'),
    details: {...wrapper.props('step').details, permitted_fill_values: []}}});
  assert.equal(wrapper.get('.completion-actions button').element.disabled, true);
  assert.match(wrapper.get('.completion-actions button').text(), /未授权/);
  wrapper.unmount();
});

test("matching backend conclusion disables repeated automatic fill", async () => {
  const {wrapper, calls} = fixture();
  await wrapper.setProps({step: {...wrapper.props('step'), result_status: "MATCH", reason: "品牌型号与材料一致"}});
  assert.match(wrapper.get('.completion-actions button').text(), /页面已一致/);
  await wrapper.get('.completion-actions button').trigger('click');
  assert.deepEqual(calls, []);
  wrapper.unmount();
});

test("unavailable completion preserves material conflicts and missing image feedback", async () => {
  const {wrapper} = fixture();
  const step = wrapper.props('step');
  await wrapper.setProps({step: {...step, details: {...step.details, permitted_fill_values: [],
    brand_model_completion: {...step.details.brand_model_completion, status: "UNAVAILABLE", completed_value: "",
      issues: ["整车型号存在不同读值", "整车型号存在不同读值"]}}}, pageData: null});
  assert.match(wrapper.get('.completion-result').text(), /暂不能确定完整品牌型号/);
  assert.equal(wrapper.findAll('.completion-warning p').length, 1);
  assert.equal(wrapper.findAll('.completion-evidence img').length, 0);
  assert.match(wrapper.text(), /未关联到材料原图/);
  assert.equal(wrapper.get('.completion-actions button').element.disabled, true);
  assert.equal(wrapper.get('.brand-manual input').element.disabled, true);
  wrapper.unmount();
});

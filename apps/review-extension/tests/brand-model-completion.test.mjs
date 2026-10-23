// @vitest-environment jsdom
import { test } from "vitest";
import assert from "node:assert/strict";
import { mount } from "@vue/test-utils";
import Detail from "../src/features/review/ui/ReviewStepDetail.vue";

function fixture(issues = []) {
  const value = "欧曼牌BJ4259SNFKB-XJ";
  const calls = [];
  const step = {
    step_id: "FIELD-vehicle.brand_model",
    category: "FIELD",
    label: "品牌型号",
    page_target_field: "vehicle.brand_model",
    result_status: "CONFLICT",
    reason: `页面品牌型号与材料组合值${value}不一致，由人工判断是否回填`,
    writable: true,
    values: [
      {
        source: "行驶证",
        document_type: "vehicle_license",
        value: "欧曼牌BJ4259SNFKB-X",
        image_id: "license",
      },
      {
        source: "铭牌",
        document_type: "vehicle_nameplate",
        value: "BJ4259SNFKB-XJ",
        image_id: "plate",
      },
      { source: "完整品牌型号", value, derived_from: "行驶证品牌文字＋完整整车型号" },
    ],
    evidence: [],
    details: {
      permitted_fill_values: [value],
      brand_model_completion: {
        license_value: "欧曼牌BJ4259SNFKB-X",
        brand_text: "欧曼牌",
        license_model: "BJ4259SNFKB-X",
        auxiliary_model: "BJ4259SNFKB-XJ",
        completed_value: value,
        status: "READY",
        basis: "行驶证品牌文字＋辅助材料完整整车型号",
        issues,
      },
    },
  };
  const wrapper = mount(Detail, {
    props: {
      step,
      disabled: false,
      qrChecks: [],
      pageData: {
        pageFields: { "vehicle.brand_model": "欧曼牌BJ4259SNFKB-X" },
        images: [
          { imageId: "license", src: "https://example.test/license.jpg" },
          { imageId: "plate", src: "https://example.test/plate.jpg" },
        ],
      },
      onFill: async (task, text) => {
        calls.push([task.page_target_field, text]);
      },
      onFocusImage: async (id) => {
        calls.push(["image", id]);
      },
      onChoose: () => {},
    },
  });
  return { wrapper, calls, value };
}

test("brand card explains completion and fills only the full branded result", async () => {
  const { wrapper, calls, value } = fixture();
  assert.equal(wrapper.get(".material-value-heading > span").text(), "材料组合值");
  assert.match(wrapper.get(".completion-result").text(), /欧曼牌BJ4259SNFKB-XJ/);
  assert.equal(wrapper.get(".completion-suffix").text(), "J");
  assert.equal(wrapper.find(".candidate-list").exists(), false);
  assert.equal(wrapper.get(".brand-manual").element.tagName, "SECTION");
  assert.equal(wrapper.get(".completion-result .completion-materials").element.tagName, "DIV");
  assert.equal(wrapper.findAll(".completion-evidence").length, 2);
  assert.deepEqual(
    wrapper.findAll(".completion-evidence img").map((image) => image.attributes("src")),
    ["https://example.test/license.jpg", "https://example.test/plate.jpg"],
  );
  assert.match(wrapper.findAll(".completion-evidence")[0].text(), /行驶证 · 品牌型号/);
  assert.match(wrapper.findAll(".completion-evidence")[1].text(), /铭牌 · 整车型号/);
  await wrapper.get(".material-value-heading button").trigger("click");
  assert.deepEqual(calls, [["vehicle.brand_model", value]]);
  await wrapper.findAll(".image-action")[0].trigger("click");
  assert.deepEqual(calls.at(-1), ["image", "license"]);
  wrapper.unmount();
});

test("model differences remain visible and the reviewer can choose to fill the combination", async () => {
  const { wrapper, calls, value } = fixture([
    "行驶证型号与辅助材料整车型号不一致，组合值供人工判断",
  ]);
  assert.match(wrapper.get(".completion-warning").text(), /行驶证型号与辅助材料整车型号不一致/);
  assert.equal(wrapper.findAll(".completion-warning p").length, 1);
  assert.equal(wrapper.get(".material-value-heading button").element.disabled, false);
  assert.equal(wrapper.get(".material-value-heading button").text(), "回填此值");
  assert.doesNotMatch(wrapper.text(), /暂不可回填|暂不可自动回填|VIN/);
  assert.deepEqual(calls, []);
  await wrapper.get(".material-value-heading button").trigger("click");
  assert.deepEqual(calls, [["vehicle.brand_model", value]]);
  assert.equal(wrapper.get(".brand-manual").attributes("aria-label"), "人工核对后填写");
  await wrapper.get(".brand-manual input").setValue("人工确认牌MODEL123");
  await wrapper.get(".brand-manual button").trigger("click");
  assert.deepEqual(calls.at(-1), ["vehicle.brand_model", "人工确认牌MODEL123"]);
  assert.equal(wrapper.props("step").result_status, "CONFLICT");
  wrapper.unmount();
});

test("completion presentation cannot authorize a value absent from permitted fill values", async () => {
  const { wrapper } = fixture();
  await wrapper.setProps({
    step: {
      ...wrapper.props("step"),
      details: { ...wrapper.props("step").details, permitted_fill_values: [] },
    },
  });
  assert.equal(wrapper.get(".material-value-heading button").element.disabled, true);
  assert.match(wrapper.get(".completion-note:last-child").text(), /未提供可回填组合值/);
  wrapper.unmount();
});

test("matching backend conclusion disables repeated automatic fill", async () => {
  const { wrapper, calls } = fixture();
  await wrapper.setProps({
    step: { ...wrapper.props("step"), result_status: "MATCH", reason: "品牌型号与材料一致" },
  });
  assert.match(wrapper.get(".completion-note:last-child").text(), /页面已一致/);
  await wrapper.get(".material-value-heading button").trigger("click");
  assert.deepEqual(calls, []);
  wrapper.unmount();
});

test("unavailable completion preserves material conflicts and missing image feedback", async () => {
  const { wrapper } = fixture();
  const step = wrapper.props("step");
  await wrapper.setProps({
    step: {
      ...step,
      details: {
        ...step.details,
        permitted_fill_values: [],
        brand_model_completion: {
          ...step.details.brand_model_completion,
          status: "UNAVAILABLE",
          completed_value: "",
          issues: ["整车型号存在不同读值", "整车型号存在不同读值"],
        },
      },
    },
    pageData: null,
  });
  assert.match(wrapper.get(".completion-result").text(), /暂不能确定完整品牌型号/);
  assert.equal(wrapper.findAll(".completion-warning p").length, 1);
  assert.equal(wrapper.findAll(".completion-evidence img").length, 0);
  assert.match(wrapper.text(), /未关联到材料原图/);
  assert.equal(wrapper.get(".material-value-heading button").element.disabled, true);
  assert.equal(wrapper.get(".brand-manual input").element.disabled, true);
  wrapper.unmount();
});

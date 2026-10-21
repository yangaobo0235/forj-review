// @vitest-environment jsdom
import { mount } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import ScopeForm from "../src/features/ScopeForm.vue";

function form() {
  const onSave = vi.fn();
  const wrapper = mount(ScopeForm, {props: {capture: {capture_id: "capture", snapshot: {
    title: "合成业务页面", page_url: "https://example.test/review", diagnostics: [],
    fields: [{candidate_id: "vin", label: "VIN", value: "SYNTHETIC", control_kind: "input"}],
    samples: [{sample_id: "license", label: "行驶证", url: "https://example.test/image.jpg"}],
  }}, templates: [{page_id: "existing", title: "Existing", region: "test", versions: ["1"]}], session: null, busy: false, onSave}});
  return {wrapper, onSave};
}

test("字段和必传材料通过真实表单操作保存，取消材料会同时撤销必传", async () => {
  const {wrapper, onSave} = form();
  try {
    const save = wrapper.get("button.primary");
    expect(save.element.disabled).toBe(true);
    await wrapper.get("select[aria-label=配置对象]").setValue("existing");
    await wrapper.get(".field-choice input").setValue(true);
    const material = wrapper.get(".material-choice > label input");
    const required = wrapper.get(".required-choice input");
    expect(required.element.disabled).toBe(true);
    await material.setValue(true);
    await required.setValue(true);
    await save.trigger("click");
    expect(onSave).toHaveBeenLastCalledWith({fields: ["vin"], samples: ["license"], required: ["license"], pageId: "existing", rollbackVersion: ""}, false);
    await material.setValue(false);
    await save.trigger("click");
    expect(onSave.mock.lastCall[0]).toMatchObject({samples: [], required: []});
  } finally {wrapper.unmount();}
});

test("正在保存时禁止再次提交", async () => {
  const {wrapper, onSave} = form();
  try {
    await wrapper.get("select[aria-label=配置对象]").setValue("existing");
    await wrapper.get(".field-choice input").setValue(true);
    await wrapper.setProps({busy: true});
    expect(wrapper.get("button.primary").element.disabled).toBe(true);
    await wrapper.get("button.primary").trigger("click");
    expect(onSave).not.toHaveBeenCalled();
  } finally {wrapper.unmount();}
});

test("未选择已接入页面时不能提交，也没有新建页面入口", async () => {
  const {wrapper, onSave} = form();
  await wrapper.get(".field-choice input").setValue(true);
  expect(wrapper.get("button.primary").element.disabled).toBe(true);
  expect(wrapper.text()).not.toContain("新增页面");
  await wrapper.get("button.primary").trigger("click");
  expect(onSave).not.toHaveBeenCalled();
  wrapper.unmount();
});

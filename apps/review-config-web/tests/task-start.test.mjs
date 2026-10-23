// @vitest-environment jsdom
import { mount } from "@vue/test-utils";
import { reactive } from "vue";
import { expect, test, vi } from "vitest";
import TaskStart from "../src/features/TaskStart.vue";

function form(detected = "") {
  const work = reactive({
    busy: "",
    messageDraft: "",
    setMessageDraft: (value) => {
      work.messageDraft = value;
    },
    templates: [{ page_id: "existing", title: "已有页面", region: "test", versions: ["1"] }],
    captures: [],
    inventory: vi.fn(),
    capture: {
      capture_id: "capture",
      snapshot: {
        detected_page_id: detected,
        title: "合成现场",
        fields: [{ candidate_id: "vin" }],
        samples: [
          { sample_id: "license", url: "https://example.test/license.png", label: "行驶证" },
        ],
        diagnostics: [],
        record_identity: {},
      },
    },
    run: vi.fn(async (_label, fn) => fn()),
    startTask: vi.fn(),
  });
  const wrapper = mount(TaskStart, { props: { work } });
  return { wrapper, work };
}

test("直接描述目标开始任务，无需逐个勾选现场字段或图片", async () => {
  const { wrapper, work } = form();
  await wrapper.get('select[aria-label="已有审核页面"]').setValue("existing");
  await wrapper.get("textarea").setValue("把 VIN 改名为车辆识别代号");
  await wrapper.get("form").trigger("submit");
  expect(work.startTask).toHaveBeenCalledWith({
    pageId: "existing",
    message: "把 VIN 改名为车辆识别代号",
    rollbackVersion: "",
  });
  expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
  expect(wrapper.text()).not.toContain("新增页面");
  wrapper.unmount();
});

test("唯一识别的已接入页面自动带入，未识别时不能猜测页面", async () => {
  const matched = form("existing");
  expect(matched.wrapper.get('select[aria-label="已有审核页面"]').element.value).toBe("existing");
  matched.wrapper.unmount();
  const { wrapper, work } = form("unknown");
  await wrapper.get("textarea").setValue("修改名称");
  expect(wrapper.get("button.primary").element.disabled).toBe(true);
  await wrapper.get("form").trigger("submit");
  expect(work.startTask).not.toHaveBeenCalled();
  await wrapper.get('select[aria-label="已有审核页面"]').setValue("existing");
  expect(wrapper.get('select[aria-label="已有审核页面"]').element.disabled).toBe(false);
  wrapper.unmount();
});

test("无需求不能提交，保存期间禁止重复创建", async () => {
  const { wrapper, work } = form("existing");
  expect(wrapper.get("button.primary").element.disabled).toBe(true);
  await wrapper.get("textarea").setValue("调整名称");
  work.busy = "正在创建";
  await wrapper.vm.$nextTick();
  expect(wrapper.get("button.primary").element.disabled).toBe(true);
  await wrapper.get("form").trigger("submit");
  expect(work.startTask).not.toHaveBeenCalled();
  wrapper.unmount();
});

test("存在图片来源局部诊断也可开始调查，没有采集不能聊天", async () => {
  const { wrapper, work } = form("existing");
  await wrapper.get("textarea").setValue("修改名称");
  work.capture.snapshot.diagnostics = ["部分图片来源不支持"];
  await wrapper.vm.$nextTick();
  expect(wrapper.get("button.primary").element.disabled).toBe(false);
  await wrapper.get("form").trigger("submit");
  expect(work.startTask).toHaveBeenCalledOnce();
  work.startTask.mockClear();
  work.capture = null;
  await wrapper.vm.$nextTick();
  expect(wrapper.get("textarea").element.disabled).toBe(true);
  await wrapper.get("form").trigger("submit");
  expect(work.startTask).not.toHaveBeenCalled();
  wrapper.unmount();
});

test("历史采集入口按页面筛选并读取现场，图片预览按需使用原始地址", async () => {
  const { wrapper, work } = form("existing");
  work.captures = [
    {
      capture_id: "old",
      title: "长春审核",
      page_id: "changchun",
      field_count: 28,
      sample_count: 8,
    },
    {
      capture_id: "other",
      title: "青岛审核",
      page_id: "qingdao",
      field_count: 20,
      sample_count: 6,
    },
  ];
  work.selectCapture = vi.fn();
  await wrapper.get('section[aria-label="选择页面采集"] .panel-heading button').trigger("click");
  await wrapper.get('input[aria-label="查找历史采集"]').setValue("长春");
  const history = wrapper.get('select[aria-label="历史采集"]');
  expect(history.text()).toContain("长春审核");
  expect(history.text()).not.toContain("青岛审核");
  await history.setValue("old");
  expect(work.selectCapture).toHaveBeenCalledWith("old");
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "图片 1")
    .trigger("click");
  expect(wrapper.get(".capture-images img").attributes("src")).toBe(
    "https://example.test/license.png",
  );
  expect(wrapper.get(".capture-images img").attributes("loading")).toBe("lazy");
  wrapper.unmount();
});

test("新任务未发送要求由工作台保存，切回开始页仍可继续", async () => {
  const { wrapper, work } = form("existing");
  await wrapper.get("textarea").setValue("尚未开始的修改要求");
  expect(work.messageDraft).toBe("尚未开始的修改要求");
  wrapper.unmount();
  const restored = mount(TaskStart, { props: { work } });
  expect(restored.get("textarea").element.value).toBe("尚未开始的修改要求");
  expect(work.startTask).not.toHaveBeenCalled();
  restored.unmount();
});

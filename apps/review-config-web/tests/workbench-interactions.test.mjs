// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { reactive } from "vue";
import { expect, test, vi } from "vitest";
import ConfigurationConversation from "../src/features/ConfigurationConversation.vue";
import ConfigurationInspector from "../src/features/ConfigurationInspector.vue";

function workbench() {
  const work = reactive({
    busy: "",
    running: false,
    messageDraft: "",
    referenceDraft: [],
    captures: [],
    source: null,
    sessionId: "task",
    events: [],
    templates: [],
    session: {
      task_id: "task",
      draft: { title: "合成审核页" },
      state: "DRAFTING",
      turn_status: "RUNNING",
      revision: 1,
      intent_revision: 1,
      messages: [],
      chat_messages: [],
      requirements: [],
      decisions: [],
      reference_fields: [],
      reference_samples: [],
      snapshot: { snapshot_id: "saved", fields: [], samples: [], diagnostics: [] },
      pending: null,
      report: null,
      receipt: null,
      task: { can_publish: false, complete: false, label: "准备修改", coverage: [] },
    },
    setMessageDraft: (value) => {
      work.messageDraft = value;
    },
    run: vi.fn(async (_label, fn) => fn()),
    send: vi.fn(async () => {}),
    action: vi.fn(),
  });
  return work;
}

test("对话和需求覆盖直接呈现后端投影，不读取原始工具消息或重算需求状态", () => {
  const work = workbench();
  work.session.messages = [{ role: "assistant", content: "内部模型原文不应展示" }];
  work.session.chat_messages = [{ role: "assistant", content: "已核对日期来源，等待业务确认。" }];
  work.session.requirements = [{ requirement_id: "raw", text: "历史原始要求", status: "ACTIVE" }];
  work.session.task.coverage = [
    { requirement_id: "current", text: "核对登记日期", resolved: true, verified: false },
  ];
  const conversation = mount(ConfigurationConversation, { props: { work } });
  const inspector = mount(ConfigurationInspector, {
    props: { work },
    global: { stubs: { AcceptanceCases: true, FieldEditor: true } },
  });
  expect(conversation.text()).toContain("已核对日期来源，等待业务确认。");
  expect(conversation.text()).not.toContain("内部模型原文不应展示");
  expect(inspector.get('[aria-label="当前有效修改要求"]').text()).toContain("核对登记日期");
  expect(inspector.text()).toContain("已处理，待验证");
  expect(inspector.text()).not.toContain("历史原始要求");
  expect(inspector.text()).not.toContain("审阅并发布");
  conversation.unmount();
  inspector.unmount();
});

test("Enter 换行、中文组字不发送，运行中允许用组合键发送更正", async () => {
  localStorage.clear();
  HTMLElement.prototype.scrollTo = vi.fn();
  const work = workbench();
  work.running = true;
  const wrapper = mount(ConfigurationConversation, { props: { work } });
  const input = wrapper.get("textarea");
  expect(input.element.disabled).toBe(false);
  await input.setValue("更正：第一项先不要改");
  await input.trigger("keydown", { key: "Enter" });
  await input.trigger("keydown", { key: "Enter", ctrlKey: true, isComposing: true });
  expect(work.send).not.toHaveBeenCalled();
  await input.trigger("keydown", { key: "Enter", ctrlKey: true, isComposing: false });
  await flushPromises();
  expect(work.send).toHaveBeenCalledWith("更正：第一项先不要改");
  expect(work.messageDraft).toBe("");
  wrapper.unmount();
});

test("澄清没有默认答案，候选与自然语言使用相同发送入口", async () => {
  const work = workbench();
  work.session.pending = {
    kind: "question",
    id: "q",
    question: "选择哪个分区？",
    options: ["旧车行驶证", "新车行驶证"],
  };
  const wrapper = mount(ConfigurationConversation, { props: { work } });
  expect(work.send).not.toHaveBeenCalled();
  await wrapper.get(".option-list button").trigger("click");
  expect(work.send).toHaveBeenCalledWith("旧车行驶证");
  wrapper.unmount();
});

test("发布面板绑定当前报告，报告或有效要求改变即关闭过期确认", async () => {
  const work = workbench();
  work.session.report = {
    ok: true,
    report_id: "report",
    candidate_digest: "digest",
    blockers: [],
    diff: {},
  };
  work.session.task.can_publish = true;
  const wrapper = mount(ConfigurationInspector, {
    props: { work },
    global: { stubs: { TaskRecovery: true, AcceptanceCases: true, FieldEditor: true } },
  });
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "审阅并发布")
    .trigger("click");
  expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
  work.session.task.can_publish = false;
  await flushPromises();
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  wrapper.unmount();
});

test("仅有发布回执时展示等待采用，不能显示任务已生效完成", () => {
  const work = workbench();
  work.session.receipt = { version: "2" };
  work.session.activation = { state: "EFFECTIVE" };
  const wrapper = mount(ConfigurationInspector, {
    props: { work },
    global: { stubs: { TaskRecovery: true, AcceptanceCases: true, FieldEditor: true } },
  });
  expect(wrapper.get('[aria-label="发布与采用核对"]').text()).toContain("已发布，继续核对实际采用");
  expect(wrapper.text()).not.toContain("配置已生效，本次任务完成");
  wrapper.unmount();
});

// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { reactive } from "vue";
import { expect, test, vi } from "vitest";
import { activityItems } from "../src/model/activity";
import ActivityTimeline from "../src/features/ActivityTimeline.vue";

const event = (seq, type, data = {}) => ({
  seq,
  type,
  run_id: "run",
  created_at: "2026-10-23T06:25:00Z",
  data,
});

test("同一次操作只显示一项，保留真实失败原因，空模型消息不成为业务进展", () => {
  const result = activityItems([
    event(1, "tool_started", {
      call_id: "read",
      name: "read_tool_result",
      activity: { title: "查阅依据", detail: "正在读取" },
    }),
    event(2, "assistant_message", { text: "" }),
    event(3, "tool_completed", {
      call_id: "read",
      name: "read_tool_result",
      status: "FAILED",
      code: "RESULT_PATH_NOT_FOUND",
      activity: { title: "查阅依据", detail: "指定路径不存在" },
    }),
  ]);
  expect(result).toHaveLength(1);
  expect(result[0]).toMatchObject({
    status: "FAILED",
    detail: "指定路径不存在",
    code: "RESULT_PATH_NOT_FOUND",
  });
});

test("结束事件关闭未返回操作，不能将中断操作显示为成功", () => {
  const result = activityItems([
    event(1, "tool_started", { call_id: "a", name: "analyze_materials" }),
    event(2, "turn_stopped", { status: "INTERRUPTED" }),
  ]);
  expect(result[0].status).toBe("INTERRUPTED");
});

test("模型请求失败保留调用编号、耗时和错误码", () => {
  const result = activityItems([
    event(1, "model_started", { model_call_id: "m" }),
    event(2, "model_failed", {
      model_call_id: "m",
      status: "FAILED",
      code: "MODEL_RATE_LIMITED",
      duration_ms: 1000,
      activity: { title: "模型请求失败", detail: "本次请求被限流" },
    }),
  ]);
  expect(result).toHaveLength(1);
  expect(result[0]).toMatchObject({
    callId: "m",
    status: "FAILED",
    code: "MODEL_RATE_LIMITED",
    durationMs: 1000,
  });
});

test("跟随新增记录，上滚暂停，返回最新恢复，加载历史保持阅读位置", async () => {
  sessionStorage.clear();
  const work = reactive({
    sessionId: "task",
    events: [event(1, "turn_started")],
    session: { revision: 0, task: {}, requirements: [] },
    hasEarlierEvents: true,
    loadEarlierEvents: vi.fn(async () => {
      work.events = [event(0, "turn_started"), ...work.events];
    }),
    run: vi.fn(async (_label, action) => action()),
  });
  const wrapper = mount(ActivityTimeline, {
    props: { work },
    global: { stubs: { TaskRecovery: true } },
  });
  const scroll = wrapper.get(".activity-scroll").element;
  Object.defineProperties(scroll, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { value: 200 },
  });
  await flushPromises();
  work.events.push(event(2, "tool_started", { call_id: "a", name: "read_draft" }));
  await flushPromises();
  expect(scroll.scrollTop).toBe(1000);
  scroll.scrollTop = 200;
  await wrapper.get(".activity-scroll").trigger("scroll");
  work.events.push(
    event(3, "tool_completed", { call_id: "a", name: "read_draft", status: "SUCCEEDED" }),
  );
  await flushPromises();
  expect(scroll.scrollTop).toBe(200);
  expect(wrapper.get(".activity-latest").text()).toContain("新进展");
  await wrapper.get(".activity-latest").trigger("click");
  await flushPromises();
  expect(scroll.scrollTop).toBe(1000);
  scroll.scrollTop = 120;
  await wrapper.get(".activity-scroll>button").trigger("click");
  await flushPromises();
  expect(work.loadEarlierEvents).toHaveBeenCalledOnce();
  expect(scroll.scrollTop).toBe(120);
  wrapper.unmount();
});

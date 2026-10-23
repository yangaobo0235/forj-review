// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { reactive } from "vue";
import { expect, test, vi } from "vitest";
import AcceptanceCases from "../src/features/AcceptanceCases.vue";
import { request } from "../src/api/client";

vi.mock("../src/api/client", () => ({ request: vi.fn() }));

test("独立预期按当前页面与版本展示，明确确认后冻结，切换任务丢弃旧预期", async () => {
  request
    .mockResolvedValueOnce({
      schema_version: "1.0",
      cases: [
        {
          name: "VIN 相同",
          request: { page_id: "page", profile_version: "1" },
          batch: {},
          expect: { recommendation: "PASS", comparisons: { vin: "MATCH" } },
        },
        {
          name: "其他页面",
          request: { page_id: "other" },
          batch: {},
          expect: { recommendation: "PASS" },
        },
      ],
    })
    .mockResolvedValueOnce({ case_set_id: "local/confirmed" });
  const work = reactive({
    busy: "",
    running: false,
    caseSets: ["baseline"],
    session: { task_id: "task", draft: { page_id: "page", version: "2" } },
    run: vi.fn(async (_name, fn) => fn()),
    inventory: vi.fn(),
    setNotice: vi.fn(),
  });
  const wrapper = mount(AcceptanceCases, { props: { work } });
  await wrapper.get("select").setValue("baseline");
  await wrapper.get("button").trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("VIN 相同");
  expect(wrapper.text()).not.toContain("其他页面");
  const freeze = wrapper.findAll("button").find((b) => b.text() === "确认并冻结独立预期");
  await wrapper.get('input[placeholder="小写字母、数字、下划线、短横线"]').setValue("confirmed");
  expect(freeze.element.disabled).toBe(true);
  await wrapper.get('input[type="checkbox"]').setValue(true);
  await freeze.trigger("click");
  await flushPromises();
  expect(request).toHaveBeenLastCalledWith(
    "/case-sets",
    expect.objectContaining({
      case_set_id: "confirmed",
      suite: expect.objectContaining({
        cases: [expect.objectContaining({ request: { page_id: "page", profile_version: "2" } })],
      }),
    }),
  );
  work.session.task_id = "different-task";
  await flushPromises();
  expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
  wrapper.unmount();
});

test("读取期间更换参考样例集时丢弃旧响应", async () => {
  let resolve;
  request.mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const work = reactive({
    busy: "",
    running: false,
    caseSets: ["first", "second"],
    session: { task_id: "task", draft: { page_id: "page", version: "2" } },
    run: vi.fn(async (_name, fn) => fn()),
  });
  const wrapper = mount(AcceptanceCases, { props: { work } });
  await wrapper.get("select").setValue("first");
  await wrapper.get("button").trigger("click");
  await wrapper.get("select").setValue("second");
  resolve({
    schema_version: "1.0",
    cases: [
      {
        name: "过期的第一组样例",
        request: { page_id: "page" },
        batch: {},
        expect: { recommendation: "PASS" },
      },
    ],
  });
  await flushPromises();
  expect(wrapper.text()).not.toContain("过期的第一组样例");
  expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
  wrapper.unmount();
});

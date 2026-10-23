// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { reactive } from "vue";
import { expect, test, vi } from "vitest";
import FieldEditor from "../src/features/FieldEditor.vue";
import { fieldChanges } from "../src/model/field-diff.ts";

function editor(activation_only = false) {
  const work = reactive({
    busy: null,
    running: false,
    caseSets: ["independent"],
    session: {
      task_id: "session",
      revision: 0,
      snapshot: { fields: [], samples: [] },
      reference_fields: [],
      reference_samples: [],
      decisions: [],
      requirements: [],
      activation_only,
      receipt: null,
      pending: null,
      field_dependencies: { "page.vin": ["记录身份"] },
      draft: {
        page_id: "existing",
        version: "2",
        scopes: ["page"],
        sections: [{ key: "page", title: "页面" }],
        materials: [{ document_type: "permit", label: "证件" }],
        fields: [{ key: "page.vin", label: "VIN", aliases: ["VIN"], evidence: [], mode: null }],
      },
    },
    manual: vi.fn(async () => {}),
    run: vi.fn(async (_label, fn) => fn()),
    action: vi.fn(),
    trial: vi.fn(),
  });
  return { work, wrapper: mount(FieldEditor, { props: { work } }) };
}

test("有身份依赖的字段展示锁定原因，删除被禁用", () => {
  const { wrapper } = editor();
  expect(wrapper.text()).toContain("记录身份");
  expect(wrapper.findAll(".field-editor-actions button")[1].element.disabled).toBe(true);
  wrapper.unmount();
});

test("没有专用依赖的系统输入可以通过普通删除操作移除", async () => {
  const { wrapper, work } = editor();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  try {
    work.session.draft.fields = [
      { key: "page.phone", label: "手机号", aliases: ["手机号"], mode: "SYSTEM", evidence: [] },
    ];
    work.session.field_dependencies = { "page.phone": ["开发接入字段"] };
    work.session.comparison_configurable_fields = ["page.phone"];
    await flushPromises();
    const remove = wrapper.findAll(".field-editor-actions button")[1];
    expect(remove.element.disabled).toBe(false);
    await remove.trigger("click");
    await flushPromises();
    expect(work.manual).toHaveBeenCalledWith("draft", {
      operations: [{ op: "remove_field", value: { key: "page.phone" } }],
    });
  } finally {
    confirm.mockRestore();
    wrapper.unmount();
  }
});

test("新增字段只发送字段操作和用户填写的证据，不声明写回权限", async () => {
  const { wrapper, work } = editor();
  await wrapper.get("button.secondary").trigger("click");
  const form = wrapper.get("form");
  await form.get('input[placeholder="例如 vehicle.engine_no"]').setValue("page.extra");
  await form.findAll("input")[1].setValue("补充字段");
  await form.get("textarea").setValue("补充字段");
  await form
    .findAll("button")
    .find((b) => b.text() === "添加材料来源")
    .trigger("click");
  await form.get("fieldset select").setValue("permit");
  await form.get("fieldset input").setValue("printed.extra");
  await form.trigger("submit");
  await flushPromises();
  expect(work.manual).toHaveBeenCalledWith("draft", {
    operations: [
      {
        op: "upsert_field",
        value: expect.objectContaining({
          key: "page.extra",
          label: "补充字段",
          evidence: [{ material: "permit", field: "printed.extra", scope: "page" }],
        }),
      },
    ],
  });
  expect(work.manual.mock.calls[0][1].operations[0].value).not.toHaveProperty("writable");
  wrapper.unmount();
});

test("普通系统输入通过显式操作接入材料比较，不夹带权限修改", async () => {
  const { wrapper, work } = editor();
  work.session.draft.fields = [
    {
      key: "page.phone",
      label: "手机号",
      aliases: ["手机号"],
      mode: "SYSTEM",
      normalizer: "default",
      evidence: [],
      section: "page",
      page_section: "page",
    },
  ];
  work.session.field_dependencies = { "page.phone": ["开发接入字段"] };
  work.session.comparison_configurable_fields = ["page.phone"];
  await flushPromises();
  await wrapper.get(".field-editor-actions button").trigger("click");
  const form = wrapper.get("form");
  await form
    .findAll("select")
    .find((s) => s.find('option[value="SINGLE_SOURCE"]').exists())
    .setValue("SINGLE_SOURCE");
  await form
    .findAll("select")
    .find((s) => s.find('option[value="phone"]').exists())
    .setValue("phone");
  await form
    .findAll("button")
    .find((b) => b.text() === "添加材料来源")
    .trigger("click");
  await form.get("fieldset select").setValue("permit");
  await form.get("fieldset input").setValue("permit.phone");
  await form.trigger("submit");
  await flushPromises();
  const operations = work.manual.mock.calls[0][1].operations;
  expect(operations).toHaveLength(2);
  expect(operations[0].op).toBe("upsert_field");
  expect(operations[0].value).not.toHaveProperty("mode");
  expect(operations[0].value).not.toHaveProperty("evidence");
  expect(operations[1]).toEqual({
    op: "set_field_comparison",
    value: {
      key: "page.phone",
      mode: "SINGLE_SOURCE",
      normalizer: "phone",
      evidence: [{ material: "permit", field: "permit.phone", scope: "page" }],
    },
  });
  expect(operations.every((o) => !Object.hasOwn(o.value, "writable"))).toBe(true);
  wrapper.unmount();
});

test("回退会话允许手动验证，已确认保存时冻结维护按钮", async () => {
  const { wrapper, work } = editor(true);
  expect(wrapper.get("button.secondary").element.disabled).toBe(true);
  const trial = wrapper.findAll("button").find((b) => b.text() === "创建只读试采集任务");
  expect(trial.element.disabled).toBe(false);
  await trial.trigger("click");
  await flushPromises();
  expect(work.manual).toHaveBeenCalledWith("trial");
  work.session.pending = { kind: "confirmation", confirmed: true };
  await flushPromises();
  expect(trial.element.disabled).toBe(true);
  wrapper.unmount();
});

test("字段差异对操作人员展示增删和具体修改项", () => {
  const diff = {
    pages: {
      changed: {
        existing: {
          fields: {
            added: { "p.new": { label: "新增项" } },
            removed: { "p.old": { label: "删除项" } },
            changed: {
              "p.vin": {
                before: { label: "VIN", aliases: ["VIN"] },
                after: { label: "识别码", aliases: ["识别码"] },
              },
            },
          },
        },
      },
    },
  };
  const changes = fieldChanges(diff);
  expect(changes.map((c) => c.kind)).toEqual(["新增", "删除", "修改"]);
  expect(changes[2].detail).toBe("名称、页面别名");
});

test("切换会话或草稿版本时丢弃正在编辑的旧字段", async () => {
  const { wrapper, work } = editor();
  await wrapper.get("button.secondary").trigger("click");
  expect(wrapper.find("form").exists()).toBe(true);
  work.session.task_id = "other-session";
  await flushPromises();
  expect(wrapper.find("form").exists()).toBe(false);
  await wrapper.get("button.secondary").trigger("click");
  work.session.revision += 1;
  await flushPromises();
  expect(wrapper.find("form").exists()).toBe(false);
  expect(work.manual).not.toHaveBeenCalled();
  wrapper.unmount();
});

test("字段差异包含已移除的可选属性", () => {
  const changes = fieldChanges({
    field_changes: {
      page_id: "existing",
      changed: {
        "p.date": { before: { label: "日期", max_age_days: 30 }, after: { label: "日期" } },
      },
    },
  });
  expect(changes[0].detail).toBe("日期范围");
});

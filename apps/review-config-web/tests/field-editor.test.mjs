// @vitest-environment jsdom
import {mount, flushPromises} from "@vue/test-utils";
import {reactive} from "vue";
import {expect, test, vi} from "vitest";
import FieldEditor from "../src/features/FieldEditor.vue";
import {fieldChanges} from "../src/model/field-diff.ts";

function editor(activation_only = false) {
  const work = reactive({busy: null, running: false, caseSets: ["independent"],
    session: {session_id: "session", revision: 0, snapshot: {fields: [], samples: []},
      selected_fields: [], selected_samples: [], decisions: [], requirements: [],
      activation_only, receipt: null, pending: null, field_dependencies: {"page.vin": ["记录身份"]},
      draft: {page_id: "existing", version: "2", scopes: ["page"], sections: [{key: "page", title: "页面"}],
        materials: [{document_type: "permit", label: "证件"}], fields: [{key: "page.vin", label: "VIN", aliases: ["VIN"], evidence: [], mode: null}]}},
    manual: vi.fn(async () => {}), run: vi.fn(async (_label, fn) => fn()),
    action: vi.fn(), trial: vi.fn(),
  });
  return {work, wrapper: mount(FieldEditor, {props: {work}})};
}

test("有身份依赖的字段展示锁定原因，删除被禁用", () => {
  const {wrapper} = editor();
  expect(wrapper.text()).toContain("记录身份");
  expect(wrapper.findAll(".field-editor-actions button")[1].element.disabled).toBe(true);
  wrapper.unmount();
});

test("新增字段只发送字段操作和用户填写的证据，不声明写回权限", async () => {
  const {wrapper, work} = editor();
  await wrapper.get("button.secondary").trigger("click");
  const form = wrapper.get("form");
  await form.get('input[placeholder="例如 vehicle.engine_no"]').setValue("page.extra");
  await form.findAll("input")[1].setValue("补充字段");
  await form.get("textarea").setValue("补充字段");
  await form.findAll("button").find(b => b.text() === "添加材料来源").trigger("click");
  await form.get("fieldset select").setValue("permit");
  await form.get("fieldset input").setValue("printed.extra");
  await form.trigger("submit");
  await flushPromises();
  expect(work.manual).toHaveBeenCalledWith("draft", {operations: [{op: "upsert_field", value: expect.objectContaining({key: "page.extra", label: "补充字段", evidence: [{material: "permit", field: "printed.extra", scope: "page"}]})}]});
  expect(work.manual.mock.calls[0][1].operations[0].value).not.toHaveProperty("writable");
  wrapper.unmount();
});

test("回退会话允许手动验证，已确认保存时冻结维护按钮", async () => {
  const {wrapper, work} = editor(true);
  expect(wrapper.get("button.secondary").element.disabled).toBe(true);
  const trial = wrapper.findAll("button").find(b => b.text() === "创建只读试采集任务");
  expect(trial.element.disabled).toBe(false);
  await trial.trigger("click");
  await flushPromises();
  expect(work.manual).toHaveBeenCalledWith("trial");
  work.session.pending = {kind: "confirmation", confirmed: true};
  await flushPromises();
  expect(trial.element.disabled).toBe(true);
  wrapper.unmount();
});

test("字段差异对操作人员展示增删和具体修改项", () => {
  const diff = {pages: {changed: {existing: {fields: {
    added: {"p.new": {label: "新增项"}}, removed: {"p.old": {label: "删除项"}},
    changed: {"p.vin": {before: {label: "VIN", aliases: ["VIN"]}, after: {label: "识别码", aliases: ["识别码"]}}},
  }}}}};
  const changes = fieldChanges(diff);
  expect(changes.map(c => c.kind)).toEqual(["新增", "删除", "修改"]);
  expect(changes[2].detail).toBe("名称、页面别名");
});


test("切换会话或草稿版本时丢弃正在编辑的旧字段", async () => {
  const {wrapper, work} = editor();
  await wrapper.get("button.secondary").trigger("click");
  expect(wrapper.find("form").exists()).toBe(true);
  work.session.session_id = "other-session";
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
  const changes = fieldChanges({field_changes: {page_id: "existing", changed: {
    "p.date": {before: {label: "日期", max_age_days: 30}, after: {label: "日期"}},
  }}});
  expect(changes[0].detail).toBe("日期范围");
});

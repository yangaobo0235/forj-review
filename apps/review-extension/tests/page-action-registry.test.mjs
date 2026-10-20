/**
 * 页面动作注册表的前端语义。
 *
 * 后端下发一个前端没有注册执行器的 `action_id` 时，以前是**静默跳过**：
 * 页面上什么都没发生，审核员也没有任何提示。这是「新增业务前端零改动」
 * 最直接的破口，所以这里锁定「至少要看得见」。
 */

import assert from "node:assert/strict";
import { test } from "vitest";

import { PageActionRegistry } from "../src/platform/extension/page-action-registry.ts";

const intent = (actionId) => ({
  action_id: actionId,
  payload: {},
  requires_authorization: false,
});

test("未注册的动作不再静默跳过，而是给出可见提示", async () => {
  const registry = new PageActionRegistry();

  const messages = await registry.executeAll([intent("brand_new_action")], {});

  assert.equal(messages.length, 1);
  assert.match(messages[0], /brand_new_action/);
  assert.match(messages[0], /尚未在前端注册/);
});

test("已注册的动作正常执行，不产生额外提示", async () => {
  const registry = new PageActionRegistry();
  registry.register("known_action", async () => "已执行");

  const messages = await registry.executeAll([intent("known_action")], {});

  assert.deepEqual(messages, ["已执行"]);
});

test("已注册动作与未注册动作同时到达时，两者都要有交代", async () => {
  const registry = new PageActionRegistry();
  registry.register("known_action", async () => "已执行");

  const messages = await registry.executeAll(
    [intent("known_action"), intent("unknown_action")],
    {},
  );

  assert.deepEqual(messages[0], "已执行");
  assert.match(messages[1], /unknown_action/);
});

test("重复注册同一个动作会报错，避免后注册的静默覆盖前一个", () => {
  const registry = new PageActionRegistry();
  registry.register("dup_action", async () => "第一次");

  assert.throws(() => registry.register("dup_action", async () => "第二次"), /已注册/);
});

test("后端没有下发任何动作时不产生提示", async () => {
  const registry = new PageActionRegistry();

  assert.deepEqual(await registry.executeAll([], {}), []);
});

test("需要授权的动作不得自动执行，重复自动动作只执行一次", async () => {
  const registry = new PageActionRegistry();
  let calls = 0;
  registry.register("known", async () => { calls++; return "已执行"; });
  const messages = await registry.executeAll([{ ...intent("known"), requires_authorization: true },
    intent("known"), intent("known")], {});
  assert.match(messages[0], /需要操作人员确认/);
  assert.equal(calls, 1);
});

test("页面动作失败不会把已完成的审核变成失败", async () => {
  const registry = new PageActionRegistry();
  registry.register("known", async () => { throw new Error("browser unavailable"); });
  assert.match((await registry.executeAll([intent("known")], {}))[0], /原页面人工操作/);
});

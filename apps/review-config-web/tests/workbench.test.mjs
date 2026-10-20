import { test, vi } from "vitest";
import assert from "node:assert/strict";
import {visibleMessages, progressLabel, questionOptions} from "../src/model/presentation.ts";
import {runCollectionTrial} from "../src/browser/bridge.ts";
import {request} from "../src/api/client.ts";

test("conversation displays business messages without exposing tool protocol or internal validation payloads", () => {
  const messages = visibleMessages({messages: [
    {role: "user", content: "审核注册日期\nrequirement_id=req1"}, {role: "assistant", content: "请确认日期含义"},
    {role: "tool", content: "private tool result"}, {role: "assistant", content: ""},
    {role: "user", content: "系统强制验证结果：{}"},
  ]});
  assert.deepEqual(messages.map(m => m.content), ["审核注册日期", "请确认日期含义"]);
  assert.equal(progressLabel([{type: "tool_started", data: {name: "record_decision"}}]), "记录字段映射依据");
});

test("workbench calls the configuration API through its own frontend proxy without credentials", async () => {
  const previous = globalThis.fetch;
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(url, "/api/review-config/sessions");
      assert.deepEqual(init.headers, {"content-type": "application/json"});
      assert.deepEqual(JSON.parse(init.body), {page_id: "synthetic"});
      return Response.json({session_id: "session"});
    };
    assert.deepEqual(await request("/sessions", {page_id: "synthetic"}), {session_id: "session"});
    globalThis.fetch = async () => new Response("upstream unavailable", {status: 502});
    await assert.rejects(request("/sessions"), /26102/);
    globalThis.fetch = async () => Response.json({detail: "Not Found"}, {status: 404});
    await assert.rejects(request("/captures"), /重启本机 26102/);
    await assert.rejects(request("/captures/missing"), /404/);
  } finally {globalThis.fetch = previous;}
});

test("web trial delegates to the captured extension and returns actionable extension errors", async () => {
  const previous = globalThis.chrome;
  try {
    delete globalThis.chrome;
    await assert.rejects(runCollectionTrial({}, {}), /Chrome/);
    globalThis.chrome = {runtime: {sendMessage: (id, message, callback) => {
      assert.equal(id, "extension"); assert.equal(message.capture_id, "capture");
      assert.equal(message.type, "CONFIGURATION_COLLECTION_TRIAL"); callback({result: {fields: {vin: 1}}});
    }}};
    assert.deepEqual(await runCollectionTrial({extension_id: "extension", capture_id: "capture"}, {id: "trial"}), {fields: {vin: 1}});
    globalThis.chrome.runtime.sendMessage = (_id, _message, callback) => callback({error: "原业务标签页已关闭"});
    await assert.rejects(runCollectionTrial({extension_id: "extension"}, {}), /已关闭/);
  } finally {globalThis.chrome = previous;}
});

test("business history uses backend question and answer projection and tolerates legacy options", () => {
  const chat_messages = [{role: "assistant", content: "日期含义？"}, {role: "user", content: "注册日期"}];
  assert.deepEqual(visibleMessages({messages: [{role: "tool", content: "internal"}], chat_messages}), chat_messages);
  assert.deepEqual(questionOptions("注册日期"), []);
  assert.deepEqual(questionOptions(["注册日期", 1, null, ""]), ["注册日期"]);
});

test("a hung request times out with lifecycle signal and a later poll can succeed", async () => {
  const previous = globalThis.fetch;
  const nativeTimeout = AbortSignal.timeout;
  vi.spyOn(AbortSignal, "timeout").mockImplementation(ms => {assert.equal(ms, 15000); return nativeTimeout(20);});
  const keepAlive = setInterval(() => {}, 50);
  try {
    const lifecycle = new AbortController();
    globalThis.fetch = (_url, {signal}) => new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => reject(signal.reason), {once: true});
    });
    await assert.rejects(request("/sessions/s", undefined, undefined, lifecycle.signal), /超时/);
    assert.equal(lifecycle.signal.aborted, false);
    globalThis.fetch = async () => Response.json({revision: 1});
    assert.deepEqual(await request("/sessions/s", undefined, undefined, lifecycle.signal), {revision: 1});
  } finally {globalThis.fetch = previous; clearInterval(keepAlive);}
});

test("switching session aborts an outstanding request without reporting a timeout", async () => {
  const previous = globalThis.fetch;
  try {
    const lifecycle = new AbortController();
    globalThis.fetch = (_url, {signal}) => new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => reject(signal.reason), {once: true});
    });
    const pending = request("/sessions/old", undefined, undefined, lifecycle.signal);
    lifecycle.abort();
    await assert.rejects(pending, error => error.name === "AbortError");
  } finally {globalThis.fetch = previous;}
});

test("timeout also bounds reading a stalled response body", async () => {
  const previous = globalThis.fetch;
  const nativeTimeout = AbortSignal.timeout;
  vi.spyOn(AbortSignal, "timeout").mockImplementation(() => nativeTimeout(20));
  const keepAlive = setInterval(() => {}, 50);
  try {
    globalThis.fetch = async (_url, {signal}) => ({status: 200, ok: true, json: () => new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => reject(signal.reason), {once: true});
    })});
    await assert.rejects(request("/sessions/s", undefined, undefined, new AbortController().signal), /超时/);
  } finally {globalThis.fetch = previous; clearInterval(keepAlive);}
});

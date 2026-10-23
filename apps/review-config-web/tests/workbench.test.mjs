import { test, vi } from "vitest";
import assert from "node:assert/strict";
import { progressLabel, questionOptions } from "../src/model/presentation.ts";
import { runCollectionTrial } from "../src/browser/bridge.ts";
import { request } from "../src/api/client.ts";
import { readTask } from "../src/api/tasks.ts";

test("progress labels describe committed tool and turn events", () => {
  assert.equal(
    progressLabel([{ type: "tool_started", data: { name: "record_decision" } }]),
    "记录字段映射依据",
  );
  assert.equal(
    progressLabel([{ type: "tool_completed", data: { name: "read_tool_result" } }]),
    "查阅相关配置依据",
  );
  assert.equal(
    progressLabel([
      { type: "tool_started", data: { name: "get_configuration" } },
      { type: "turn_stopped", data: { status: "INTERRUPTED" } },
    ]),
    "本轮处理已中断",
  );
});

test("workbench calls the configuration API through its own frontend proxy without credentials", async () => {
  const previous = globalThis.fetch;
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(url, "/api/review-config/tasks");
      assert.deepEqual(init.headers, { "content-type": "application/json" });
      assert.deepEqual(JSON.parse(init.body), { page_id: "synthetic" });
      return Response.json({ task_id: "session" });
    };
    assert.deepEqual(await request("/tasks", { page_id: "synthetic" }), { task_id: "session" });
    globalThis.fetch = async () => new Response("upstream unavailable", { status: 502 });
    await assert.rejects(request("/tasks"), /26102/);
    globalThis.fetch = async () => Response.json({ detail: "Not Found" }, { status: 404 });
    await assert.rejects(request("/captures"), /重启本机 26102/);
    await assert.rejects(request("/captures/missing"), /404/);
  } finally {
    globalThis.fetch = previous;
  }
});

test("web trial delegates to the captured extension and returns actionable extension errors", async () => {
  const previous = globalThis.chrome;
  try {
    delete globalThis.chrome;
    await assert.rejects(runCollectionTrial({}, {}), /Chrome/);
    globalThis.chrome = {
      runtime: {
        sendMessage: (id, message, callback) => {
          assert.equal(id, "extension");
          assert.equal(message.capture_id, "capture");
          assert.equal(message.type, "CONFIGURATION_COLLECTION_TRIAL");
          callback({ result: { fields: { vin: 1 } } });
        },
      },
    };
    assert.deepEqual(
      await runCollectionTrial(
        { extension_id: "extension", capture_id: "capture" },
        { id: "trial" },
      ),
      { fields: { vin: 1 } },
    );
    globalThis.chrome.runtime.sendMessage = (_id, _message, callback) =>
      callback({ error: "原业务标签页已关闭" });
    await assert.rejects(runCollectionTrial({ extension_id: "extension" }, {}), /已关闭/);
  } finally {
    globalThis.chrome = previous;
  }
});

test("task reads require backend projections and preserve the supplied business conversation", async () => {
  const chat_messages = [
    { role: "assistant", content: "日期含义？" },
    { role: "user", content: "按材料核对\nrequirement_id=业务原文" },
  ];
  const task = {
    stage: "preparation",
    progress_step: 1,
    label: "候选已暂存",
    complete: false,
    can_publish: false,
    coverage: [{ requirement_id: "req", text: "核对日期", resolved: true, verified: false }],
  };
  const previous = globalThis.fetch;
  try {
    const detail = { task, chat_messages, messages: [{ role: "tool", content: "internal" }] };
    globalThis.fetch = async () => Response.json(detail);
    assert.deepEqual(await readTask("task"), detail);
    for (const invalid of [
      { ...detail, task: undefined },
      { ...detail, chat_messages: undefined },
      { ...detail, chat_messages: detail.messages },
      { ...detail, task: { ...task, can_publish: "true" } },
      { ...detail, task: { ...task, coverage: [{ text: "缺少后端判定" }] } },
    ]) {
      globalThis.fetch = async () => Response.json(invalid);
      await assert.rejects(readTask("task"), /任务视图不完整/);
    }
  } finally {
    globalThis.fetch = previous;
  }
});

test("question choices tolerate older option representations", () => {
  assert.deepEqual(questionOptions("注册日期"), []);
  assert.deepEqual(questionOptions(["注册日期", 1, null, ""]), ["注册日期"]);
});

test("a hung request times out with lifecycle signal and a later poll can succeed", async () => {
  const previous = globalThis.fetch;
  const nativeTimeout = AbortSignal.timeout;
  vi.spyOn(AbortSignal, "timeout").mockImplementation((ms) => {
    assert.equal(ms, 15000);
    return nativeTimeout(20);
  });
  const keepAlive = setInterval(() => {}, 50);
  try {
    const lifecycle = new AbortController();
    globalThis.fetch = (_url, { signal }) =>
      new Promise((resolve, reject) => {
        signal.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    await assert.rejects(request("/tasks/s", undefined, undefined, lifecycle.signal), /超时/);
    assert.equal(lifecycle.signal.aborted, false);
    globalThis.fetch = async () => Response.json({ revision: 1 });
    assert.deepEqual(await request("/tasks/s", undefined, undefined, lifecycle.signal), {
      revision: 1,
    });
  } finally {
    globalThis.fetch = previous;
    clearInterval(keepAlive);
  }
});

test("switching session aborts an outstanding request without reporting a timeout", async () => {
  const previous = globalThis.fetch;
  try {
    const lifecycle = new AbortController();
    globalThis.fetch = (_url, { signal }) =>
      new Promise((resolve, reject) => {
        signal.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    const pending = request("/tasks/old", undefined, undefined, lifecycle.signal);
    lifecycle.abort();
    await assert.rejects(pending, (error) => error.name === "AbortError");
  } finally {
    globalThis.fetch = previous;
  }
});

test("timeout also bounds reading a stalled response body", async () => {
  const previous = globalThis.fetch;
  const nativeTimeout = AbortSignal.timeout;
  vi.spyOn(AbortSignal, "timeout").mockImplementation(() => nativeTimeout(20));
  const keepAlive = setInterval(() => {}, 50);
  try {
    globalThis.fetch = async (_url, { signal }) => ({
      status: 200,
      ok: true,
      json: () =>
        new Promise((resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason), { once: true });
        }),
    });
    await assert.rejects(
      request("/tasks/s", undefined, undefined, new AbortController().signal),
      /超时/,
    );
  } finally {
    globalThis.fetch = previous;
    clearInterval(keepAlive);
  }
});

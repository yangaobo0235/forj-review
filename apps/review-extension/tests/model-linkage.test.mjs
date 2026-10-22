// @vitest-environment jsdom
import {test, vi, onTestFinished} from "vitest";
import assert from "node:assert/strict";
import {observeModelLinkage} from "../src/platform/browser/actions/model-linkage.ts";

function fixture() {
  vi.useFakeTimers();
  onTestFinished(() => vi.useRealTimers());
  const state = {callback: null, disposed: false, current: true};
  const root = {defaultView: {performance: {now: () => 100}, location: {origin: "https://example.test", href: "https://example.test/review"},
    setTimeout, clearTimeout, PerformanceObserver: class {
      constructor(callback) {state.callback = callback;}
      observe() {}
      disconnect() {state.disposed = true;}
    }}};
  const observation = observeModelLinkage(root, () => state.current);
  const resource = overrides => ({name: "https://example.test/api/admin/vehicle-model/config/detail",
    startTime: 101, initiatorType: "xmlhttprequest", responseStatus: 200, ...overrides});
  const emit = (...entries) => state.callback({getEntries: () => entries});
  return {state, observation, resource, emit};
}

test("only a new same-origin model config response releases dependent field writes", async () => {
  const {state, observation, resource, emit} = fixture();
  let released = false;
  const pending = observation.wait().then(result => {released = true; return result;});
  emit(resource({startTime: 99}), resource({name: "https://foreign.test/vehicle-model/config/detail"}),
    resource({name: "https://example.test/vehicle-model/select"}));
  await vi.advanceTimersByTimeAsync(1000);
  assert.equal(released, false);
  emit(resource());
  await vi.advanceTimersByTimeAsync(0);
  assert.equal((await pending).ok, true);
  observation.dispose();
  assert.equal(state.disposed, true);
  assert.equal(vi.getTimerCount(), 0);
});

test.each(["timeout", "failed", "changed"])("model linkage %s fails without authorizing spec writes", async kind => {
  const {state, observation, resource, emit} = fixture();
  const pending = observation.wait();
  if (kind === "timeout") await vi.advanceTimersByTimeAsync(15000);
  else {
    if (kind === "changed") state.current = false;
    emit(resource(kind === "failed" ? {responseStatus: 500} : {}));
  }
  const result = await pending;
  assert.equal(result.ok, false);
  assert.equal(result.code, kind === "timeout" ? "MODEL_LINKAGE_TIMEOUT" : kind === "failed" ? "MODEL_LINKAGE_FAILED" : "PAGE_IDENTITY_CHANGED");
  observation.dispose();
  assert.equal(vi.getTimerCount(), 0);
});

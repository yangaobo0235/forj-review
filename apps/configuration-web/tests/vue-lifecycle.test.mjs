import test from "node:test";
import assert from "node:assert/strict";
import { effectScope } from "vue";
import { useWorkbench } from "../src/model/use-workbench.ts";
import { renderMarkdown } from "../src/ui/markdown.ts";

const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};
async function until(check) {
  for (let i = 0; i < 100; i++) { if (check()) return; await new Promise(done => setTimeout(done, 1)); }
  assert.fail("client lifecycle did not settle");
}
function harness(t, handler, search = "?session=first") {
  const saved = {fetch: globalThis.fetch, location: globalThis.location, history: globalThis.history};
  const calls = [];
  globalThis.location = {search};
  globalThis.history = {replaceState() {}};
  globalThis.fetch = async (url, init) => {
    const path = String(url).replace("/api/configuration-agent", "");
    calls.push({path, ...init});
    if (path === "/sessions" || path === "/captures") return Response.json([]);
    if (path === "/templates") return Response.json({templates: []});
    if (path.includes("/events?")) return Response.json([]);
    const value = await handler(path, init);
    return value instanceof Response ? value : Response.json(value);
  };
  const scope = effectScope();
  const work = scope.run(() => useWorkbench());
  t.after(() => {
    scope.stop();
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
    }
  });
  return {work, scope, calls};
}
const session = (id, revision = 1, extra = {}) => ({session_id: id, revision, state: "DRAFTING", turn_status: "IDLE", capture_id: "", ...extra});

test("Vue session watcher aborts the old poll and discards its late result", async t => {
  const old = deferred();
  let oldSignal;
  const {work} = harness(t, (path, init) => {
    if (path === "/sessions/first") { oldSignal = init.signal; return old.promise; }
    assert.equal(path, "/sessions/second");
    return session("second");
  });
  await until(() => oldSignal);
  work.navigate("second");
  assert.equal(oldSignal.aborted, true);
  await until(() => work.session?.session_id === "second");
  old.resolve(session("first", 99));
  await new Promise(done => setTimeout(done, 0));
  assert.equal(work.session.session_id, "second");
  assert.equal(work.connectionError, "");
});

test("disposing a Vue scope cancels the active poll and prevents a late update", async t => {
  const pending = deferred();
  let signal;
  const {work, scope} = harness(t, (_path, init) => { signal = init.signal; return pending.promise; });
  await until(() => signal);
  scope.stop();
  assert.equal(signal.aborted, true);
  pending.resolve(session("first"));
  await new Promise(done => setTimeout(done, 0));
  assert.equal(work.session, null);
  assert.equal(work.connectionError, "");
});

test("Vue operation lock rejects duplicate clicks before the next render", async t => {
  const pending = deferred();
  const {work} = harness(t, () => ({}), "");
  let count = 0;
  const first = work.run("发送消息", async () => { count++; await pending.promise; });
  await work.run("发送消息", async () => { count++; });
  assert.equal(count, 1);
  assert.equal(work.busy, "发送消息");
  pending.resolve(); await first;
  assert.equal(work.busy, "");
});

test("question answers preserve the backend revision and update Vue session state", async t => {
  let revision = 4;
  const {work, calls} = harness(t, path => {
    if (path.endsWith("/answer")) { revision = 5; return {}; }
    return session("first", revision, {pending: revision === 4 ? {kind: "question", id: "q1"} : null});
  });
  await until(() => work.session?.revision === 4);
  await work.run("发送消息", () => work.send("使用注册日期"));
  const sent = calls.find(call => call.path.endsWith("/answer"));
  assert.equal(sent.path, "/sessions/first/interactions/q1/answer");
  assert.equal(JSON.parse(sent.body).expected_revision, 4);
  assert.equal(JSON.parse(sent.body).answer, "使用注册日期");
  assert.equal(work.session.revision, 5);
  assert.equal(work.running, false);
});

test("Markdown keeps tables but cannot execute HTML, unsafe links or remote images", () => {
  const html = renderMarkdown('| 字段 | 材料 |\n| --- | --- |\n| 注册日期 | 行驶证 |\n\n<script>alert(1)</script>\n\n[危险](javascript:alert(1))\n\n![材料](https://example.test/private.png)');
  assert.match(html, /<table>/);
  assert.doesNotMatch(html, /<script|href="javascript:|<img/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /材料/);
});

test("an older poll cannot overwrite a command result at the same draft revision", async t => {
  const delayed = deferred();
  let firstRead = true;
  const {work} = harness(t, path => {
    if (path.endsWith('/messages')) return {};
    if (firstRead) { firstRead = false; return delayed.promise; }
    return session('first', 4, {turn_status: 'RUNNING'});
  });
  await until(() => !firstRead);
  work.session = session('first', 4);
  await work.send('核对注册日期');
  assert.equal(work.session.turn_status, 'RUNNING');
  delayed.resolve(session('first', 4, {turn_status: 'COMPLETED'}));
  await new Promise(done => setTimeout(done, 0));
  assert.equal(work.session.turn_status, 'RUNNING');
});

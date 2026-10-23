import assert from "node:assert/strict";
import { test } from "vitest";

import { pollReviewJob } from "../src/features/review/api/review-jobs.ts";

test("polls running snapshots until a terminal snapshot", async () => {
  const snapshots = [
    { status: "RUNNING", progress: { completed_count: 1, total_count: 6 } },
    { status: "RUNNING", progress: { completed_count: 4, total_count: 6 } },
    { status: "COMPLETED", progress: { completed_count: 6, total_count: 6 } },
  ];
  const seen = [];

  const result = await pollReviewJob(
    async () => snapshots.shift(),
    (snapshot) => seen.push(snapshot.progress.completed_count),
    { timeoutMs: 1000, intervalMs: 0 },
  );

  assert.equal(result.status, "COMPLETED");
  assert.deepEqual(seen, [1, 4, 6]);
});

test("returns the latest running snapshot when the client deadline is reached", async () => {
  let now = 0;
  const snapshot = { status: "RUNNING", progress: { completed_count: 3, total_count: 6 } };

  const result = await pollReviewJob(
    async () => snapshot,
    () => {},
    {
      timeoutMs: 10,
      intervalMs: 0,
      now: () => (now += 6),
    },
  );

  assert.equal(result.progress.completed_count, 3);
});

test("a stalled snapshot cannot bypass the overall deadline", async () => {
  const snapshot = { status: "RUNNING", progress: { completed_count: 1, total_count: 2 } };
  let calls = 0;
  const result = await pollReviewJob(
    () => (++calls === 1 ? Promise.resolve(snapshot) : new Promise(() => {})),
    () => {},
    { timeoutMs: 25, intervalMs: 0 },
  );
  assert.equal(result, snapshot);
});

test("cancelled polling ignores a late response", async () => {
  const controller = new AbortController();
  let resolve;
  const seen = [];
  const polling = pollReviewJob(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
    (snapshot) => seen.push(snapshot),
    { signal: controller.signal, timeoutMs: 1000 },
  );
  controller.abort();
  await assert.rejects(polling, { name: "AbortError" });
  resolve({ status: "COMPLETED" });
  await Promise.resolve();
  assert.deepEqual(seen, []);
});

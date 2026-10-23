import assert from "node:assert/strict";
import { test } from "vitest";
import { logDiagnostic } from "../src/shared/diagnostics.ts";

test("diagnostics keep counts and correlation, dropping payloads and injection", () => {
  const records = [];
  const original = console.info;
  console.info = (...args) => records.push(args);
  try {
    logDiagnostic("collect.complete", {
      job_id: "job-123",
      collectionId: "collection:1",
      imageCount: 3,
      ok: false,
      url: "https://example.test?token=SECRET",
      message: "PRIVATE",
      value: "PRIVATE",
      fields: { name: "PRIVATE" },
      code: "FORGED\nline",
      durationMs: Infinity,
      privateNumber: 123456,
    });
  } finally {
    console.info = original;
  }
  assert.deepEqual(records, [
    [
      "[ForjReview] collect.complete",
      {
        job_id: "job-123",
        collectionId: "collection:1",
        imageCount: 3,
        ok: false,
      },
    ],
  ]);
});

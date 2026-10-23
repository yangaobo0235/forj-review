import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

test("review check evidence uses the shared evidence contract", () => {
  const source = readFileSync(
    new URL("../src/shared/contracts/review.ts", import.meta.url),
    "utf8",
  );

  assert.match(source, /interface CheckResult[\s\S]*evidence\?: EvidenceFact\[\]/);
  assert.doesNotMatch(source, /evidence\?: unknown\[\]/);
  assert.match(source, /field\?: string/);
  assert.match(source, /source_id\?: string/);
});

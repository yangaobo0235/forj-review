import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("cross-document checks show conclusions without image evidence", () => {
  const source = readFileSync(
    new URL("../src/features/review/ui/ReviewAdvice.vue", import.meta.url),
    "utf8",
  );

  assert.match(source, /defineProps<\{ review: ReviewResponse \}>/);
  assert.doesNotMatch(source, /(?:imagesById|onFocusImage)/s);
  assert.match(source, /跨资料校验[\s\S]*<CheckValues :check="check" \/>/);
  assert.doesNotMatch(source, /check\.evidence/);
});

test("review check evidence uses the shared evidence contract", () => {
  const source = readFileSync(new URL("../src/shared/contracts/review.ts", import.meta.url), "utf8");

  assert.match(source, /interface CheckResult[\s\S]*evidence\?: EvidenceFact\[\]/);
  assert.doesNotMatch(source, /evidence\?: unknown\[\]/);
  assert.match(source, /field\?: string/);
  assert.match(source, /source_id\?: string/);
});

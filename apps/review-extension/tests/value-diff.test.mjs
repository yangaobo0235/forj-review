import assert from "node:assert/strict";
import { test } from "vitest";
import { diffValue, diffValueByPosition } from "../src/features/review/presentation/value-diff.ts";

test("marks only differing characters", () => {
  assert.deepEqual(diffValue("LJ11R9DE9E3291975", "LJ11R9DF9F3291975"), [
    { text: "LJ11R9D", changed: false },
    { text: "E", changed: true },
    { text: "9", changed: false },
    { text: "E", changed: true },
    { text: "3291975", changed: false },
  ]);
});

test("position diff does not realign repeated characters", () => {
  assert.deepEqual(diffValueByPosition("ABCA", "ACBA"), [
    { text: "A", changed: false },
    { text: "BC", changed: true },
    { text: "A", changed: false },
  ]);
});

test("position diff marks an extra tail", () => {
  assert.deepEqual(diffValueByPosition("ABCX", "ABC"), [
    { text: "ABC", changed: false },
    { text: "X", changed: true },
  ]);
});

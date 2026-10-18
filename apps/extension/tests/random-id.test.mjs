import assert from "node:assert/strict";
import test from "node:test";
import { createRandomId } from "../src/shared/random-id.ts";

test("native UUID is used with the original crypto receiver", () => {
  const source = {randomUUID() {assert.equal(this, source); return "native-uuid";}};
  assert.equal(createRandomId(source), "native-uuid");
});

test("missing secure randomness reports an actionable error rather than inventing an unsafe token", () => {
  assert.throws(() => createRandomId({}), /安全随机标识/);
});

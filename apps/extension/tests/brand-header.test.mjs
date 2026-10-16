import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("uses the approved concise browser and in-page branding", () => {
  const manifest = JSON.parse(
    readFileSync(new URL("../public/manifest.json", import.meta.url), "utf8"),
  );
  const app = readFileSync(new URL("../src/app/App.vue", import.meta.url), "utf8");

  assert.equal(manifest.name, "赋界资料审核工作台");
  assert.equal(manifest.action.default_title, "打开赋界资料审核工作台");
  assert.match(app, /资料审核工作台/);
  assert.match(app, /材料识别与字段核对/);
  assert.doesNotMatch(app, /赋界科技 · 审核工具/);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("uses the approved concise browser and in-page branding", () => {
  const manifest = JSON.parse(
    readFileSync(new URL("../public/manifest.json", import.meta.url), "utf8"),
  );
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

  assert.equal(manifest.name, "赋界审核助手");
  assert.equal(manifest.action.default_title, "打开赋界审核助手");
  assert.match(app, /车辆智能审核/);
  assert.match(app, /识别资料差异，辅助人工复核/);
  assert.doesNotMatch(app, /赋界科技 · 审核工具/);
});

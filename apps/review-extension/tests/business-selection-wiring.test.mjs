import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

test("content collection accepts a manual business override", () => {
  const source = readFileSync(new URL("../src/entrypoints/content.ts", import.meta.url), "utf8");

  assert.match(source, /message\.businessSelection/);
  assert.match(source, /ReviewBusinessDetector\.resolve/);
  assert.match(source, /businessType/);
});

test("side panel sends the selected business during collection", () => {
  const appSource = readFileSync(new URL("../src/app/App.vue", import.meta.url), "utf8");
  const workflowSource = readFileSync(
    new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url),
    "utf8",
  );
  const configSource = readFileSync(
    new URL("../src/features/review/model/review-panel.ts", import.meta.url),
    "utf8",
  );

  assert.match(appSource, /businessSelection/);
  assert.match(appSource, /businessSelection\.value\s*=/);
  assert.match(configSource, /自动识别/);
  assert.match(workflowSource, /businessSelection/);
  assert.match(workflowSource, /state\.review\s*=\s*null/);
});

test("workflow focuses original images on the collected tab with collected page identity", () => {
  const workflowSource = readFileSync(
    new URL("../src/features/review/model/use-review-workflow.ts", import.meta.url),
    "utf8",
  );
  const focusSource = workflowSource.slice(workflowSource.indexOf("const focusOriginalImage"));

  assert.doesNotMatch(focusSource, /tabs\.query/);
  assert.match(focusSource, /focusReviewImage\(imageId, pageData\)/);
});

test("standalone preview reports that browser extension APIs are required", () => {
  const source = readFileSync(
    new URL("../src/features/review/api/collect-page.ts", import.meta.url),
    "utf8",
  );

  assert.match(source, /请在浏览器扩展侧边栏中使用/);
  assert.match(source, /globalThis\.chrome\?\.tabs/);
});

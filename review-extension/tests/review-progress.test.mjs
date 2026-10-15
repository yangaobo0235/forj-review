import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadSource } from "./tsx-loader.mjs";

const { ReviewProgress } = loadSource("components/ReviewProgress.tsx");

function render(progress = {}, overrides = {}, props = {}) {
  return renderToStaticMarkup(React.createElement(ReviewProgress, {
    job: { status: "RUNNING", phase: "RECOGNIZING", progress: {
      total_count: 4, submitted_count: 4, completed_count: 0, failed_count: 0, timed_out_count: 0,
      ...progress,
    }, ...overrides }, ...props,
  }));
}

test("submitting every URL does not advance image processing", () => {
  const html = render();
  assert.match(html, /aria-valuenow="0"/);
  assert.match(html, /图片已处理 0\/4/);
  assert.doesNotMatch(html, /已提交|已识别/);
  assert.match(render({ completed_count: 1 }), /aria-valuenow="25"/);
});

test("failed and timed out images count as processed but not recognized successfully", () => {
  const html = render({ completed_count: 1, failed_count: 1, timed_out_count: 1 });
  assert.match(html, /aria-valuenow="75"/);
  assert.match(html, /图片已处理 3\/4 · 识别成功 1 张/);
  assert.match(html, /失败 1 张，超时 1 张/);
  assert.match(html, /刷新原页面后重新审核/);
});

test("finishing image processing does not claim the review conclusion is ready", () => {
  const html = render({ completed_count: 4 }, { phase: "FINALIZING" });
  assert.match(html, /aria-valuenow="100"/);
  assert.match(html, /正在生成审核结论/);
  assert.doesNotMatch(html, /审核已完成/);
});

test("a finished partial review still calls for manual review", () => {
  const html = render({ completed_count: 3, failed_count: 1 }, { status: "PARTIAL", phase: "COMPLETED" });
  assert.match(html, /审核部分完成，需人工复核/);
  assert.doesNotMatch(html, /审核已完成/);
});

test("an empty image list has a finite progress value throughout finalization", () => {
  assert.match(render({ total_count: 0 }, { phase: "PREPARING" }), /aria-valuenow="0"/);
  assert.match(render({ total_count: 0 }, { phase: "FINALIZING" }), /aria-valuenow="100"/);
});

test("failure list identifies only affected materials and offers the original image", () => {
  const html = render({ completed_count: 1, failed_count: 1, timed_out_count: 1 }, {
    failed_image_ids: ["failed"], timed_out_image_ids: ["timeout"],
  }, {
    pageData: { images: [
      { index: 0, imageId: "success", alt: "成功材料" },
      { index: 1, imageId: "failed", groupTitle: "报废机动车回收证明" },
      { index: 2, imageId: "timeout", alt: "机动车销售发票" },
    ] },
    onFocusImage: async () => ({ ok: true }),
  });
  assert.match(html, /第 2 张 · 报废机动车回收证明：识别失败/);
  assert.match(html, /第 3 张 · 机动车销售发票：识别超时/);
  assert.doesNotMatch(html, /成功材料/);
  assert.equal((html.match(/查看原图<\/button>/g) ?? []).length, 2);
});

test("collection failures remain actionable with a backend that only returns counts", () => {
  const html = render({ failed_count: 1 }, {}, {
    pageData: { images: [{ index: 0, imageId: "unreadable", groupTitle: "行驶证", collectionError: "unavailable" }] },
    onFocusImage: async () => ({ ok: true }),
  });
  assert.match(html, /行驶证：暂时无法读取/);
  assert.match(html, /查看原图<\/button>/);
  assert.doesNotMatch(html, /unavailable/);
});

test("images without a DOM identifier can be listed but do not offer an unusable focus action", () => {
  const html = render({ failed_count: 1 }, { failed_image_ids: ["0"] }, {
    pageData: { images: [{ index: 0, groupTitle: "身份证" }] },
    onFocusImage: async () => ({ ok: true }),
  });
  assert.match(html, /身份证：识别失败/);
  assert.doesNotMatch(html, /查看原图<\/button>/);
});

import { classifyImage } from "../src/platform/browser/collection/material-hints.ts";
import assert from "node:assert/strict";
import { test } from "vitest";
import { readFileSync } from "node:fs";
import { ReviewImageCandidates } from "../src/platform/browser/collection/image-candidates.ts";

function loadSelector() {
  return ReviewImageCandidates.select;
}

const candidate = (index, overrides = {}) => ({
  index,
  src: `https://example.test/${index}.jpg`,
  visible: true,
  naturalWidth: 1200,
  naturalHeight: 800,
  className: "document-preview",
  role: "",
  ariaHidden: false,
  emptySlot: false,
  hint: "旧车资料",
  categoryHint: "old_vehicle",
  ...overrides,
});

test("excludes decorations and hidden images but keeps identity cards", () => {
  const select = loadSelector();
  const result = select(
    [
      candidate(0, { visible: false }),
      candidate(1, { className: "company-logo", hint: "", categoryHint: "unknown" }),
      candidate(2, { role: "presentation", hint: "", categoryHint: "unknown" }),
      candidate(5, { emptySlot: true, hint: "新车资料 暂无图片", categoryHint: "new_vehicle" }),
      candidate(3, { hint: "身份证", categoryHint: "identity_card" }),
      candidate(4),
    ],
    6,
  );

  assert.deepEqual(
    Array.from(result.selected, (item) => item.index),
    [3, 4],
  );
  assert.equal(result.overflow, false);
});

test("does not discard a real image only because its broader hint mentions an empty slot", () => {
  const select = loadSelector();
  const result = select([
    candidate(1, {
      emptySlot: false,
      hint: "新车资料 已上传 机动车行驶证 暂无图片",
      categoryHint: "new_vehicle",
    }),
  ]);

  assert.deepEqual(
    Array.from(result.selected, (item) => item.index),
    [1],
  );
});

test("keeps at most six original indices while covering available document types", () => {
  const select = loadSelector();
  const input = [
    candidate(9, { hint: "", categoryHint: "unknown" }),
    ...Array.from({ length: 7 }, (_, offset) => candidate(offset + 20)),
  ];

  const result = select(input, 6);

  assert.deepEqual(
    Array.from(result.selected, (item) => item.index),
    [20, 21, 22, 23, 24, 9],
  );
  assert.equal(result.overflow, true);
  assert.equal(result.scannedCount, 8);
});

test("keeps a registration certificate as a known business document", () => {
  const select = loadSelector();
  const result = select([
    candidate(1, {
      hint: "机动车登记证书",
      categoryHint: "registration_certificate",
      naturalWidth: 100,
      naturalHeight: 100,
    }),
  ]);

  assert.deepEqual(
    Array.from(result.selected, (item) => item.index),
    [1],
  );
});

test("keeps business licenses but excludes unrelated large images", () => {
  const select = loadSelector();
  const result = select([
    candidate(1, {
      businessScope: "business_license",
      hint: "营业执照",
      categoryHint: "business_license",
      naturalWidth: 2400,
      naturalHeight: 1600,
    }),
    candidate(2, { businessScope: "old_vehicle" }),
    candidate(3, { businessScope: "new_vehicle", hint: "新车资料", categoryHint: "new_vehicle" }),
    candidate(4, { businessScope: "other", hint: "其他图片", categoryHint: "unknown" }),
  ]);

  assert.deepEqual(new Set(Array.from(result.selected, (item) => item.index)), new Set([1, 2, 3]));
});

test("reserves one image for each business scope and document type", () => {
  const select = loadSelector();
  const input = [
    ...Array.from({ length: 10 }, (_, index) =>
      candidate(index, {
        businessScope: "old_vehicle",
        categoryHint: "registration_certificate",
        hint: "机动车登记证书",
        naturalWidth: 2000 - index,
      }),
    ),
    candidate(20, {
      businessScope: "new_vehicle",
      categoryHint: "invoice",
      hint: "发票",
      naturalWidth: 100,
      naturalHeight: 100,
    }),
  ];

  const result = select(input, 10);

  assert.equal(result.selected.length, 10);
  assert.ok(result.selected.some((item) => item.categoryHint === "invoice"));
  assert.equal(result.overflow, true);
});

test("fills remaining slots with the highest-ranked extra pages", () => {
  const select = loadSelector();
  const input = [
    candidate(1, {
      businessScope: "old_vehicle",
      categoryHint: "registration_certificate",
      hint: "机动车登记证书",
      naturalWidth: 1600,
    }),
    candidate(2, {
      businessScope: "old_vehicle",
      categoryHint: "registration_certificate",
      hint: "机动车登记证书",
      naturalWidth: 1500,
    }),
    candidate(3, {
      businessScope: "old_vehicle",
      categoryHint: "registration_certificate",
      hint: "机动车登记证书",
      naturalWidth: 1400,
    }),
    candidate(4, {
      businessScope: "new_vehicle",
      categoryHint: "invoice",
      hint: "发票",
      naturalWidth: 100,
      naturalHeight: 100,
    }),
  ];

  const result = select(input, 3);

  assert.deepEqual(
    Array.from(result.selected, (item) => item.index),
    [1, 2, 4],
  );
});

test("content collector reserves enough capacity for fixed and conditional materials", () => {
  const content = readFileSync(new URL("../src/entrypoints/content.ts", import.meta.url), "utf8");

  assert.match(content, /const MAX_REVIEW_IMAGES = 16;/);
  assert.match(content, /ReviewImageCandidates\.select\(imageCandidates, MAX_REVIEW_IMAGES\)/);
  assert.match(content, /候选资料超过 \$\{MAX_REVIEW_IMAGES\} 张/);
});

test("content collector classifies invoices before generic new-vehicle groups", () => {
  assert.equal(classifyImage("新车资料 发票"), "invoice");
});

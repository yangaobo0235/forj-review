import { imageDocumentType } from "../src/platform/browser/collection/material-hints.ts";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { applyCollectManifest } from "../src/platform/browser/catalog/collect-manifest.ts";
import { documentTypeForImageLabel, imageLabelFor, nearestAncestorText } from "../src/platform/browser/collection/image-label.ts";

const vehicleSource = JSON.parse(
  readFileSync(new URL("./fixtures/collect-manifest-vehicle-source.json", import.meta.url), "utf8"),
);
const transfer = JSON.parse(
  readFileSync(new URL("./fixtures/collect-manifest-transfer.json", import.meta.url), "utf8"),
);

/**
 * 极简假 DOM：只实现 image-label.ts 用到的 parentElement / textContent /
 * querySelectorAll("img") / ownerDocument。不引 jsdom，保持测试零依赖。
 */
const el = (textContent, imgCount, parentElement = null) => ({
  textContent,
  parentElement,
  ownerDocument: { body: null },
  querySelectorAll: (selector) => (selector === "img" ? Array.from({ length: imgCount }, () => ({})) : []),
});

/** 页面真实结构：分组容器 → 图片卡片 → 空壳预览层 → img，小标题在卡片里。 */
const cardImage = (label, groupText) => {
  const group = el(groupText, 4);
  const card = el(label, 1, group);
  const box = el("", 1, card);
  return { image: { parentElement: box, ownerDocument: { body: null } }, group, card, box };
};

test("小标题从图片卡片读取，跳过只包着 img 的空壳", () => {
  const { image } = cardImage(" 行驶证反面 ", "证件照 行驶证反面 登记证书1、2页 登记证书3、4页 行驶证正面");
  assert.equal(imageLabelFor(image), "行驶证反面");
});

test("多图分组不会被当成小标题", () => {
  // 没有卡片层：空壳之上直接是装着 4 张图的分组。小标题必须取不到，
  // 否则会拿一整段拼接文本去判类型。
  const group = el("证件照 行驶证反面 登记证书1、2页", 4);
  const box = el("", 1, group);
  const image = { parentElement: box, ownerDocument: { body: null } };
  assert.equal(imageLabelFor(image), "");
  // 兜底文本仍然能拿到上层内容，不会退化成空字符串。
  assert.equal(nearestAncestorText(image), "证件照 行驶证反面 登记证书1、2页");
});

test("单图容器但文字过长时不冒充小标题", () => {
  const long = `行驶证反面 ${"补充说明".repeat(20)}`;
  const card = el(long, 1);
  const image = { parentElement: card, ownerDocument: { body: null } };
  assert.equal(imageLabelFor(image), "");
});

test("vehicle-source 的小标题直接定类型，不依赖上传顺序", () => {
  applyCollectManifest(vehicleSource);
  assert.equal(documentTypeForImageLabel("行驶证反面"), "vehicle_license");
  assert.equal(documentTypeForImageLabel("行驶证正面"), "vehicle_license");
  assert.equal(documentTypeForImageLabel("登记证书1、2页"), "registration_certificate");
  assert.equal(documentTypeForImageLabel("登记证书3、4页"), "registration_certificate");
  assert.equal(documentTypeForImageLabel("未知材料"), null);
});

test("transfer 的小标题直接定类型，二手车发票不串到报废置换", () => {
  applyCollectManifest(transfer);
  assert.equal(documentTypeForImageLabel("登记证书1、2页"), "registration_certificate");
  assert.equal(documentTypeForImageLabel("二手车发票"), "used_car_invoice");
  assert.equal(documentTypeForImageLabel("未知材料"), null);
});


test("trial and formal material hints prioritize card labels over positional slots", () => {
  applyCollectManifest(transfer);
  assert.equal(imageDocumentType({labelHint: "二手车发票", businessScope: "transfer", groupOrder: 1,
    categoryHint: "registration_certificate"}), "used_car_invoice");
  assert.equal(imageDocumentType({labelHint: "未知", businessScope: "transfer", categoryHint: "registration_certificate"}),
    "registration_certificate");
});

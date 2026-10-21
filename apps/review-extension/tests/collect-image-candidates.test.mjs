import assert from "node:assert/strict";
import { test } from "vitest";
import { collectImageCandidates } from "../src/platform/browser/collection/collect-image-candidates.ts";
import { classifyImage, imageDocumentType } from "../src/platform/browser/collection/material-hints.ts";
import { ReviewImageCandidates } from "../src/platform/browser/collection/image-candidates.ts";

test("报废页其他图片区内的旧新车分组均被采集，无单图标签时不按顺序猜类型", () => {
  // 按实际页面结构构造脱敏 DOM：其他图片 > 两个分区 > ant-image > img。
  class Element {
    constructor(text = "", children = []) {
      this.nodeType = 1;
      this.childNodes = [...(text ? [{ nodeType: 3, textContent: text }] : []), ...children];
      this.children = children;
      for (const child of children) child.parentElement = this;
    }
    get textContent() { return this.childNodes.map(child => child.textContent).join(""); }
    querySelectorAll(selector) {
      const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
      return selector === "img" ? descendants.filter(child => child instanceof Image) : descendants;
    }
    getAttribute() { return null; }
    getBoundingClientRect() { return { width: 400, height: 300 }; }
  }
  class Image extends Element {
    constructor(id) { super(); this.src = `https://example.test/${id}.jpg`; this.alt = ""; this.title = ""; this.naturalWidth = 800; this.naturalHeight = 600; }
  }
  const section = (title, prefix) => new Element("", [new Element(title), ...[0, 1, 2].map(i => new Element("", [new Image(`${prefix}-${i}`)]))]);
  const body = new Element("", [new Element("身份证正面 暂无图片"), new Element("身份证反面 暂无图片"),
    new Element("营业执照", [new Element("", [new Image("business")])]),
    new Element("", [new Element("其他图片"), section("报废车辆资料", "old"), section("新车及发票信息", "new")]),
  ]);
  const document = { body, images: body.querySelectorAll("img") };
  for (const element of [body, ...body.querySelectorAll("*")]) element.ownerDocument = document;
  const previous = new Map(["document", "window", "Node", "HTMLImageElement"].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  try {
    Object.assign(globalThis, { document, window: { getComputedStyle: () => ({ display: "block", visibility: "visible" }) }, Node: { TEXT_NODE: 3 }, HTMLImageElement: Image });
    const candidates = collectImageCandidates(classifyImage);
    assert.equal(ReviewImageCandidates.select(candidates, 16).selected.length, 7);
    assert.deepEqual(candidates.map(item => item.businessScope), ["business_license", ...Array(3).fill("old_vehicle"), ...Array(3).fill("new_vehicle")]);
    assert.deepEqual(candidates.slice(1).map(imageDocumentType), ["vehicle_license", "registration_certificate", "scrap_certificate", "vehicle_license", "registration_certificate", "invoice"]);
    assert.equal(imageDocumentType(candidates[0]), "business_license");
    assert.ok(candidates.slice(4).every(item => item.categoryHint === "new_vehicle"));
  } finally {
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});

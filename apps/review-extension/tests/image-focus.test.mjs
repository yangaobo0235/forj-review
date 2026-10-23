import assert from "node:assert/strict";
import { test } from "vitest";
import { ReviewImageFocus } from "../src/platform/browser/actions/image-focus.ts";

function loadImageFocus() {
  return ReviewImageFocus;
}

test("scrolls to, highlights, and opens the collected image", () => {
  const { focus } = loadImageFocus();
  const scrollCalls = [];
  const events = [];
  const timers = [];
  let clickCount = 0;
  let outlineAtClick = "";
  const image = {
    isConnected: true,
    style: { outline: "1px solid red", outlineOffset: "0px" },
    scrollIntoView(options) {
      scrollCalls.push(options);
      events.push("scroll");
    },
    click() {
      clickCount += 1;
      outlineAtClick = this.style.outline;
      events.push("click");
    },
  };

  const result = focus(image, (callback, delay) => timers.push({ callback, delay }));

  assert.deepEqual({ ...result }, { ok: true });
  assert.deepEqual(
    { ...scrollCalls[0] },
    {
      behavior: "smooth",
      block: "center",
      inline: "nearest",
    },
  );
  assert.deepEqual(events, ["scroll", "click"]);
  assert.equal(clickCount, 1);
  assert.equal(outlineAtClick, "4px solid #1677ff");
  assert.equal(image.style.outline, "4px solid #1677ff");
  assert.equal(timers[0].delay, 2000);
  timers[0].callback();
  assert.equal(image.style.outline, "1px solid red");
  assert.equal(image.style.outlineOffset, "0px");
});

test("缩略图的可见容器一起高亮并滚动，小图也能被看见", () => {
  const { focus } = loadImageFocus();
  const container = {
    tagName: "LI",
    style: { outline: "", outlineOffset: "" },
    scrollIntoView() {
      scrollCalls.push("container");
    },
  };
  const scrollCalls = [];
  const image = {
    isConnected: true,
    tagName: "IMG",
    style: { outline: "", outlineOffset: "" },
    closest: () => container,
    scrollIntoView() {
      scrollCalls.push("image");
    },
    click() {},
  };

  const result = focus(image, () => {});

  assert.deepEqual({ ...result }, { ok: true });
  // 滚动落在容器上：缩略图自己滚到屏幕中间，上下文反而看不见。
  assert.deepEqual(scrollCalls, ["container"]);
  assert.equal(container.style.outline, "4px solid #1677ff");
  assert.equal(image.style.outline, "4px solid #1677ff");
});

test("容器是 body 级别时不描边，避免整页蓝框", () => {
  const { focus } = loadImageFocus();
  const body = { tagName: "BODY", style: { outline: "", outlineOffset: "" }, scrollIntoView() {} };
  const image = {
    isConnected: true,
    tagName: "IMG",
    style: { outline: "", outlineOffset: "" },
    closest: () => body,
    scrollIntoView() {},
    click() {},
  };

  focus(image, () => {});

  assert.equal(body.style.outline, "");
  assert.equal(image.style.outline, "4px solid #1677ff");
});

test("does not click an original image that is no longer connected", () => {
  const { focus } = loadImageFocus();
  let clickCount = 0;
  const image = {
    isConnected: false,
    click() {
      clickCount += 1;
    },
  };

  assert.deepEqual({ ...focus(image) }, { ok: false, error: "原图已变化，请重新采集" });
  assert.deepEqual({ ...focus(null) }, { ok: false, error: "原图已变化，请重新采集" });
  assert.equal(clickCount, 0);
});

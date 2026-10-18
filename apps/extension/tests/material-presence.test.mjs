import assert from "node:assert/strict";
import test from "node:test";
import { createRenderer, h, nextTick } from "vue";
import { renderToString } from "vue/server-renderer";
import { loadSource } from "./vue-loader.mjs";
import { classifyImage, identitySidesForLabel } from "../src/platform/browser/collection/material-hints.ts";
import { createUrlReviewJob } from "../src/features/review/api/review-client.ts";

const { default: MaterialPresence } = loadSource("features/review/ui/MaterialPresence.vue");
const item = (name, status, ids = []) => ({ key: name, display_name: name, status, image_ids: ids, reason: "请核对原图" });
const render = (report) => renderToString(h(MaterialPresence, { report, onFocusImage: () => {} }));

test("展开、收起和查看原图的实际事件保留当前材料图片 ID", async () => {
  const node = (type, text = "") => ({ type, text, children: [], props: {} });
  const renderer = createRenderer({
    createElement: node, createText: text => node("text", text), createComment: text => node("comment", text),
    setText: (el, text) => { el.text = text; }, setElementText: (el, text) => { el.text = text; },
    patchProp: (el, key, _old, value) => { el.props[key] = value; },
    insert: (child, parent) => { child.parent = parent; parent.children.push(child); },
    remove: el => { el.parent.children = el.parent.children.filter(child => child !== el); },
    parentNode: el => el.parent, nextSibling: () => null,
  });
  const focused = [];
  const root = node("root");
  const app = renderer.createApp(MaterialPresence, {
    report: { status: "COMPLETE", notices: [], groups: [{ key: "old", display_name: "旧车", status: "PRESENT",
      alternatives: [[item("行驶证", "PRESENT", ["old_vehicle-03"])]] }] },
    onFocusImage: id => { focused.push(id); },
  });
  app.mount(root);
  const find = (el, type) => el.type === type ? el : el.children.map(child => find(child, type)).find(Boolean);
  const details = find(root, "details");
  assert.equal(details.props.open, false);
  details.props.onToggle({ target: { open: true } });
  await nextTick();
  assert.equal(details.props.open, true);
  find(root, "button").props.onClick();
  assert.deepEqual(focused, ["old_vehicle-03"]);
  details.props.onToggle({ target: { open: false } });
  await nextTick();
  assert.equal(details.props.open, false);
  app.unmount();
});

test("完整性区完全展示服务端种类与替代方案，已满足组不把可选材料报成缺失", async () => {
  const html = await render({ status: "COMPLETE", notices: [], groups: [{
    key: "subject", display_name: "主体资料", status: "PRESENT", alternatives: [
      [item("营业执照", "PRESENT", ["license-01"])],
      [item("身份证正面", "MISSING"), item("身份证反面", "MISSING")],
    ],
  }] });
  assert.doesNotMatch(html, /<details[^>]*\sopen(?:[\s=>])/);
  assert.match(html, /材料齐全/);
  assert.match(html, /未提供（可选）/);
  assert.match(html, /满足任一方案即可/);
  assert.match(html, /aria-label="营业执照，原图 1"/);
  assert.match(html, /字段识别结果在下方核对/);
});

test("有缺项与待确认默认展开，待确认图片保留原图入口", async () => {
  for (const status of ["INCOMPLETE", "UNCERTAIN"]) {
    const html = await render({ status, notices: ["页面图片超过采集上限"], groups: [{
      key: "arbitrary", display_name: "服务端材料分组", status: "UNCERTAIN",
      alternatives: [[item("服务端材料名称", "UNCERTAIN", ["unknown-01", "unknown-02"]), item("缺少的材料", "MISSING")]],
    }] });
    assert.match(html, /<details[^>]*\sopen(?:[\s=>])/);
    assert.match(html, /待确认/);
    assert.match(html, /缺少的材料/);
    assert.match(html, /查看待确认图片 2/);
    assert.match(html, /页面图片超过采集上限/);
  }
});

test("面别只认配对标签，不从上传说明或其他图片的标签猜测", () => {
  assert.deepEqual(identitySidesForLabel("身份证正面"), ["FRONT"]);
  assert.deepEqual(identitySidesForLabel("身份证（反面）"), ["BACK"]);
  assert.deepEqual(identitySidesForLabel("身份证正反面"), []);
  assert.deepEqual(identitySidesForLabel("身份证正面 暂无图片 身份证反面 营业执照"), []);
  assert.equal(classifyImage("新车及发票信息"), "new_vehicle");
  assert.equal(classifyImage("报废车辆资料"), "old_vehicle");
});

test("请求保留面别标签、稳定图片 ID 与原始签名 URL", async () => {
  let body;
  const src = "https://example.test/image.jpg?signature=synthetic%2Btoken";
  await createUrlReviewJob({ images: [{ index: 0, imageId: "identity-01", src, identitySidesHint: ["FRONT"] }] },
    async (_url, init) => { body = JSON.parse(init.body); return new Response(JSON.stringify({ job_id: "synthetic" })); });
  assert.equal(body.images[0].src, src);
  assert.equal(body.images[0].imageId, "identity-01");
  assert.deepEqual(body.images[0].identitySidesHint, ["FRONT"]);
});

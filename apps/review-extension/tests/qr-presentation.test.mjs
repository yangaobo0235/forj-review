import assert from "node:assert/strict";
import { test } from "vitest";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";
import QrUrl from "../src/features/review/ui/QrUrl.vue";

const officialUrl = "https://qclt.mofcom.gov.cn/deal/scrap/validdata/example";

function render(overrides = {}) {
  return renderToString(
    h(QrUrl, {
      check: {
        url: officialUrl,
        raw_value: "raw-qr-content",
        domain_valid: true,
        accessible: true,
        page_fields: {},
        status: "MATCH",
        ...overrides,
      },
    }),
  );
}

test("only a confirmed official URL becomes an external link", async () => {
  const html = await render();
  assert.ok(html.includes(`href="${officialUrl}"`));
  assert.match(html, /target="_blank" rel="noreferrer noopener"/);
  for (const domain_valid of [false, null]) {
    const rejected = await render({ domain_valid });
    assert.doesNotMatch(rejected, /<a /);
    assert.match(rejected, /未取得已确认的官网网址/);
  }
});

test("confirmed URL remains available for manual review when the backend cannot access it", async () => {
  for (const accessible of [false, null]) {
    const html = await render({ accessible, status: "REVIEW_REQUIRED", message: "官网访问超时" });
    assert.ok(html.includes(`href="${officialUrl}"`));
    assert.match(html, /后台暂未访问成功。可点击网址人工核验/);
    assert.match(html, /官网访问超时/);
  }
});

test("raw content and decoder diagnostics are confined to expandable technical details", async () => {
  const html = await render({
    decoder: "zxing",
    decode_error: "decode_failed",
    decode_elapsed_ms: 42,
  });
  const details = html.match(/<details class="qr-technical-details">([\s\S]*?)<\/details>/)?.[1];
  assert.ok(details);
  assert.match(details, /ZXing/);
  assert.match(details, /decode_failed/);
  assert.match(details, /42 ms/);
  assert.match(details, /raw-qr-content/);
  assert.doesNotMatch(html.slice(0, html.indexOf("<details")), /raw-qr-content/);
  assert.doesNotMatch(await render({ raw_value: null }), /<details/);
});

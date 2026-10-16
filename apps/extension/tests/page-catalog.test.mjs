/**
 * 页面识别清单：后端下发、浏览器暂存与匹配。
 *
 * 清单不可用时的行为单独在这里验：识别退回内置表，共用地址只报「认不出来」，
 * 要求人工选择，不猜。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  appliedPageCatalog,
  applyPageCatalog,
  fetchPageCatalog,
  matchPageIdentity,
} from "../src/platform/browser/catalog/page-catalog.ts";
import { ReviewBusinessDetector } from "../src/platform/browser/identity/business-detector.ts";

const catalog = JSON.parse(
  readFileSync(new URL("./fixtures/page-catalog.json", import.meta.url), "utf8"),
);

test("后端下发的清单字段与浏览器读取的一致", () => {
  applyPageCatalog(catalog);

  assert.equal(appliedPageCatalog().version, "1.0");
  // 同一个地址在清单里出现多条声明，这是共用地址能被区分的前提。
  const shared = appliedPageCatalog().identities
    .filter((item) => item.paths.includes("/consistency-qingdao"));
  assert.deepEqual(
    shared.map((item) => item.business_type).sort(),
    ["consistency", "transfer", "vehicle_source"],
  );
});

test("结构不对的清单当没下发，不半信半疑地用", () => {
  applyPageCatalog(catalog);
  applyPageCatalog({ version: "1.0", identities: [{ business_type: "transfer" }] });
  assert.equal(appliedPageCatalog(), null);

  applyPageCatalog({ version: "1.0", identities: [{ business_type: "transfer", region: "default", paths: ["/review"], anchors: [], review_tab: 1 }] });
  assert.equal(appliedPageCatalog(), null);

  applyPageCatalog(catalog);
  applyPageCatalog(null);
  assert.equal(appliedPageCatalog(), null);
});

test("清单不可用时退回内置表，共用地址要求人工选择", () => {
  applyPageCatalog(null);

  // 地址唯一的业务照常识别。
  assert.equal(
    ReviewBusinessDetector.detect("https://admin.forjtruck.com/scrap-replace-qingdao", "").businessType,
    "scrap_replacement",
  );
  // 共用地址给不出答案：内置表只有地址，猜一半错。这里必须落到人工选择。
  assert.deepEqual(
    { ...ReviewBusinessDetector.resolve("https://admin.forjtruck.com/consistency-qingdao", "新车信息 车辆所有人类型 新车挂靠") },
    { business: null, error: "无法识别当前审核业务，请人工选择" },
  );
});

test("清单拉不到时返回 null，不抛错", async () => {
  assert.equal(
    await fetchPageCatalog("http://127.0.0.1:1", async () => { throw new Error("offline"); }),
    null,
  );
  assert.equal(
    await fetchPageCatalog("http://127.0.0.1:1", async () => ({ ok: false, json: async () => ({}) })),
    null,
  );
  assert.equal(
    await fetchPageCatalog("http://127.0.0.1:1", async () => ({ ok: true, json: async () => ({ identities: [] }) })),
    null,
  );
});

test("清单拉取成功时原样返回", async () => {
  const fetched = await fetchPageCatalog("http://127.0.0.1:1", async (url) => {
    assert.equal(url, "http://127.0.0.1:1/api/review/page-catalog");
    return { ok: true, json: async () => catalog };
  });

  assert.equal(fetched.identities.length, catalog.identities.length);
});

test("匹配不修改清单本身", () => {
  const before = JSON.stringify(catalog);
  matchPageIdentity(catalog, "https://admin.forjtruck.com/consistency-qingdao", "新车信息 车辆所有人类型 新车挂靠");
  assert.equal(JSON.stringify(catalog), before);
});

test("tab declarations take precedence even with a single path candidate", () => {
  const single = {version: "1.0", identities: [catalog.identities.find(item => item.business_type === "vehicle_source")]};
  assert.equal(matchPageIdentity(single, "https://admin.forjtruck.com/vehicle-source"), null);
  assert.equal(matchPageIdentity(single, "https://admin.forjtruck.com/vehicle-source", "", {label: "车源审核", panelReady: true}).businessType, "vehicle_source");
});

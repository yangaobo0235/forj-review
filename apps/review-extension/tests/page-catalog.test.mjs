/**
 * 页面识别清单：后端下发、浏览器暂存与匹配。
 *
 * 清单不可用时不恢复历史页面身份；共用地址必须能唯一匹配。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

import {
  appliedPageCatalog,
  applyPageCatalog,
  fetchPageCatalog,
  matchPageIdentity,
} from "../src/platform/browser/catalog/page-catalog.ts";
import { ReviewBusinessDetector } from "../src/platform/browser/identity/business-detector.ts";

const catalog = JSON.parse(
  readFileSync(new URL("./fixtures/page-catalog-synthetic.json", import.meta.url), "utf8"),
);

test("后端下发的清单字段与浏览器读取的一致", () => {
  applyPageCatalog(catalog);

  assert.equal(appliedPageCatalog().version, "1.0");
  // 同一个地址在清单里出现多条声明，这是共用地址能被区分的前提。
  const shared = appliedPageCatalog().identities.filter((item) =>
    item.paths.includes("/sample-review-qingdao"),
  );
  assert.deepEqual(shared.map((item) => item.business_type).sort(), [
    "permit_consistency",
    "permit_final",
    "permit_initial",
  ]);
});

test("结构不对的清单当没下发，不半信半疑地用", () => {
  applyPageCatalog(catalog);
  applyPageCatalog({ version: "1.0", identities: [{ business_type: "permit_final" }] });
  assert.equal(appliedPageCatalog(), null);

  applyPageCatalog({
    version: "1.0",
    identities: [
      {
        business_type: "permit_final",
        region: "default",
        paths: ["/review"],
        anchors: [],
        review_tab: 1,
      },
    ],
  });
  assert.equal(appliedPageCatalog(), null);

  applyPageCatalog(catalog);
  applyPageCatalog(null);
  assert.equal(appliedPageCatalog(), null);
});

test("清单不可用时不使用历史页面身份", () => {
  applyPageCatalog(null);
  assert.equal(
    ReviewBusinessDetector.detect("https://admin.forjtruck.com/scrap-replace-qingdao", ""),
    null,
  );
  assert.equal(
    ReviewBusinessDetector.resolve(
      "https://admin.forjtruck.com/sample-review-qingdao",
      "新车信息 车辆所有人类型 新车挂靠",
    ).business,
    null,
  );
});

test("清单拉不到时返回 null，不抛错", async () => {
  assert.equal(
    await fetchPageCatalog("http://127.0.0.1:1", async () => {
      throw new Error("offline");
    }),
    null,
  );
  assert.equal(
    await fetchPageCatalog("http://127.0.0.1:1", async () => ({
      ok: false,
      json: async () => ({}),
    })),
    null,
  );
  assert.equal(
    await fetchPageCatalog("http://127.0.0.1:1", async () => ({
      ok: true,
      json: async () => ({ identities: [] }),
    })),
    null,
  );
});

test("缺失目录时审核页签也不能恢复历史配置", () => {
  applyPageCatalog(null);
  for (const city of ["qingdao", "changchun"]) {
    for (const label of ["车源审核", "过户审核", "一致性审核"]) {
      assert.equal(
        ReviewBusinessDetector.resolve(
          `https://admin.forjtruck.com/consistency-${city}`,
          "",
          null,
          { label, panelReady: true },
        ).business,
        null,
      );
    }
  }
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
  matchPageIdentity(
    catalog,
    "https://admin.forjtruck.com/sample-review-qingdao",
    "新车信息 车辆所有人类型 新车挂靠",
  );
  assert.equal(JSON.stringify(catalog), before);
});

test("tab declarations take precedence even with a single path candidate", () => {
  const single = {
    version: "1.0",
    identities: [catalog.identities.find((item) => item.business_type === "permit_initial")],
  };
  assert.equal(matchPageIdentity(single, "https://admin.forjtruck.com/sample-review"), null);
  assert.equal(
    matchPageIdentity(single, "https://admin.forjtruck.com/sample-review", "", {
      label: "初审",
      panelReady: true,
    }).businessType,
    "permit_initial",
  );
});

test("shipped catalog keeps all review steps while transfer rules stay reset", () => {
  const shipped = JSON.parse(
    readFileSync(new URL("./fixtures/page-catalog.json", import.meta.url), "utf8"),
  );
  for (const label of ["过户审核"]) {
    assert.equal(
      matchPageIdentity(shipped, "https://admin.forjtruck.com/consistency-qingdao", "", {
        label,
        panelReady: true,
      }).businessType,
      "transfer",
    );
  }
  assert.equal(
    matchPageIdentity(shipped, "https://admin.forjtruck.com/consistency-qingdao", "", {
      label: "车源审核",
      panelReady: true,
    }).businessType,
    "vehicle_source",
  );
  assert.equal(
    matchPageIdentity(shipped, "https://admin.forjtruck.com/consistency-changchun", "", {
      label: "车源审核",
      panelReady: false,
    }),
    null,
  );
  assert.equal(
    matchPageIdentity(shipped, "https://admin.forjtruck.com/vehicle-source", "", {
      label: "车源审核",
      panelReady: true,
    }),
    null,
  );
  assert.equal(
    matchPageIdentity(shipped, "https://admin.forjtruck.com/consistency-qingdao", "", {
      label: "一致性审核",
      panelReady: false,
    }),
    null,
  );
  assert.equal(
    matchPageIdentity(shipped, "https://admin.forjtruck.com/consistency-qingdao", "", {
      label: "一致性审核",
      panelReady: true,
    }).businessType,
    "consistency",
  );
});

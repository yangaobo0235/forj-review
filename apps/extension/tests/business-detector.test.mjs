import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { ReviewBusinessDetector } from "../src/platform/browser/identity/business-detector.ts";
import { applyPageCatalog } from "../src/platform/browser/catalog/page-catalog.ts";

/**
 * 页面识别清单由后端生成（`build_collect_manifest` 的邻居 `page_catalog()`），
 * 这里读的是同一份产物。识别只认清单——不认浏览器里的内置地址表。
 */
const catalog = JSON.parse(
  readFileSync(new URL("./fixtures/page-catalog.json", import.meta.url), "utf8"),
);

function loadDetector() {
  applyPageCatalog(catalog);
  return ReviewBusinessDetector;
}

// 真实页面的文字摘录。两个业务共用 /consistency-qingdao，只能靠这些文字区分。
const CONSISTENCY_PAGE_TEXT = "身份证正面 其他图片 旧车资料 新车资料 过户详情 新车信息 旧车类型 "
  + "新车类型 经销商 发票代码 发票号码 识别车架号 所有人 注册日期 终端证件号 旧车车架号 新车车架号 "
  + "新车车牌号 车辆所有人类型 旧车挂靠 新车挂靠 审核处理 立即提交";
const TRANSFER_PAGE_TEXT = "过户资料 登记证书1、2页 二手车发票 订单信息 订单编号 当前状态 审核过户凭证 "
  + "经销商 车源编号 车源名称 主机厂 开票日期 车牌号 识别车架号 过户发票买家名称 证件号 发票代码 "
  + "发票号码 开票金额(含税) 卖方名称 卖方证件号 厂牌型号 转入地车管所 二手车市场 纳税人识别号 "
  + "过户发票买卖方历史成交 审核处理 立即提交";

test("detects known business routes", () => {
  const { detect } = loadDetector();

  assert.deepEqual(
    { ...detect("http://localhost:5173/scrap-replace-qingdao", "") },
    {
      businessType: "scrap_replacement",
      region: "qingdao",
      profileVersion: "1.0",
      workflowStage: "scrap_replacement",
      selectionMode: "AUTO",
      detectionStatus: "CONFIRMED",
    },
  );
  assert.equal(
    detect("http://localhost:5173/vehicle-source", "").businessType,
    "vehicle_source",
  );
  assert.deepEqual(
    { ...detect("https://admin.forjtruck.com/scrap-replace-changchun?showPageModel=1", "") },
    {
      businessType: "scrap_replacement",
      region: "changchun",
      profileVersion: "1.0",
      workflowStage: "scrap_replacement",
      selectionMode: "AUTO",
      detectionStatus: "CONFIRMED",
    },
  );
  assert.equal(
    detect("https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1", "").region,
    "qingdao",
  );
});

test("tells the two businesses sharing one address apart by page text", () => {
  const { detect } = loadDetector();

  // 青岛和长春两个地址挂的是同一条声明：这两个业务不分地区，两地页面用的是
  // 同一套规则，所以两条地址都解析到同一个默认地区。
  for (const url of [
    "https://admin.forjtruck.com/consistency-qingdao?showPageModel=1",
    "https://admin.forjtruck.com/consistency-changchun",
  ]) {
    assert.deepEqual(
      { ...detect(url, CONSISTENCY_PAGE_TEXT) },
      {
        businessType: "consistency",
        region: "default",
        profileVersion: "1.0",
        workflowStage: "consistency",
        selectionMode: "AUTO",
        detectionStatus: "CONFIRMED",
      },
      url,
    );
    assert.equal(detect(url, TRANSFER_PAGE_TEXT).businessType, "transfer", url);
    assert.equal(detect(url, TRANSFER_PAGE_TEXT).region, "default", url);
  }
});

test("refuses to pick between two businesses sharing one address when text is unclear", () => {
  const { detect } = loadDetector();
  const url = "https://admin.forjtruck.com/consistency-qingdao?showPageModel=1";

  // 两个业务的文字一个都没命中，或者都命中（改版把两边的区块挪到一起了）：
  // 一律不猜。猜错的后果是拿错的规则去审单子，不会报错。
  assert.equal(detect(url, ""), null);
  assert.equal(detect(url, "审核处理 审核结果 通过 驳回 取消 立即提交"), null);
  assert.equal(detect(url, `${CONSISTENCY_PAGE_TEXT} ${TRANSFER_PAGE_TEXT}`), null);
  // 只命中一半也一样：特征要求全部出现，不是命中任一。
  assert.equal(detect(url, "新车信息 车辆所有人类型"), null);
  assert.equal(detect(url, "审核过户凭证 过户发票买家名称"), null);
});

test("the shared address never falls back to the built-in route table", () => {
  const { detect } = loadDetector();
  // 清单可用时只认清单。内置表对这条地址没有答案，退回内置表等于把「不猜」
  // 作废——内置表只有地址，必然给出一个答案，而那个答案对一半的页面是错的。
  assert.equal(detect("https://admin.forjtruck.com/consistency-qingdao", ""), null);
});

test("matches complete route segments instead of similar prefixes", () => {
  const { detect } = loadDetector();

  assert.equal(detect("https://admin.forjtruck.com/scrap-replace-qingdao-old", ""), null);
  assert.equal(detect("https://admin.forjtruck.com/scrap-replace-qingdao/detail/1", "").region, "qingdao");
});

test("rejects a manual choice that conflicts with the detected page", () => {
  const { resolve } = loadDetector();
  const manual = {
    businessType: "scrap_replacement",
    region: "qingdao",
    profileVersion: "1.0",
    workflowStage: "scrap_replacement",
  };

  assert.deepEqual(
    { ...resolve("https://admin.forjtruck.com/scrap-replace-changchun", "", manual) },
    {
      business: null,
      error: "人工选择的审核业务与当前页面不一致，请重新确认",
    },
  );
  // 共用地址上页面说得清楚时同样拒绝：在一致性审核页上手动选过户审核，
  // 仍然是拿错的规则审单子。
  assert.deepEqual(
    { ...resolve("https://admin.forjtruck.com/consistency-qingdao", CONSISTENCY_PAGE_TEXT, {
      businessType: "transfer",
      region: "default",
      profileVersion: "1.0",
      workflowStage: "transfer",
    }) },
    { business: null, error: "人工选择的审核业务与当前页面不一致，请重新确认" },
  );
});

test("lets a manual choice win when the page cannot be told apart", () => {
  const { resolve } = loadDetector();

  // 自动识别给不出唯一答案时人工选择必须生效，否则共用地址的两个业务永远
  // 选不进去——自动识别只会说「这是一致性」，人工选过户会被当成冲突拒绝。
  const resolved = resolve("https://admin.forjtruck.com/consistency-qingdao", "审核处理 取消 立即提交", {
    businessType: "transfer",
    region: "default",
    profileVersion: "1.0",
    workflowStage: "transfer",
  });

  assert.equal(resolved.error, null);
  assert.equal(resolved.business.businessType, "transfer");
  assert.equal(resolved.business.selectionMode, "MANUAL");
});

test("asks for a manual choice when nothing is recognised", () => {
  const { resolve } = loadDetector();

  assert.deepEqual(
    { ...resolve("http://localhost:5173/other", "普通页面") },
    { business: null, error: "无法识别当前审核业务，请人工选择" },
  );
});

test("says which page words are missing when a shared address cannot be told apart", () => {
  const { resolve } = loadDetector();

  // 识别错和识别不出来都不报错，所以这句话是排查的唯一线索：只写"无法识别"，
  // 审核员只能猜，排查的人拿不到任何信息。
  const { business, error } = resolve(
    "https://admin.forjtruck.com/consistency-qingdao",
    "审核处理 审核结果 通过 驳回 取消 立即提交",
  );

  assert.equal(business, null);
  assert.match(error, /对应 2 个审核业务/);
  assert.match(error, /consistency\/default 缺「新车信息」「车辆所有人类型」「新车挂靠」/);
  assert.match(error, /transfer\/default 缺「审核过户凭证」「过户发票买家名称」「转入地车管所」/);
});

test("names only the missing words when one side is partly there", () => {
  const { resolve } = loadDetector();

  // 过户页面缺了「转入地车管所」（页面在下面，可能没渲染出来）——这时要说清
  // 缺的是哪一个，而不是笼统地说认不出来。
  const { error } = resolve(
    "https://admin.forjtruck.com/consistency-qingdao",
    "审核过户凭证 过户发票买家名称",
  );

  assert.match(error, /transfer\/default 缺「转入地车管所」/);
  assert.doesNotMatch(error, /transfer\/default 缺「审核过户凭证」/);
});

test("falls back to page anchors only when the address is unknown", () => {
  const { detect } = loadDetector();

  // 车源审核不分地区：地址改版后靠业务声明里的页面特征兜底。
  assert.deepEqual(
    { ...detect("http://localhost:5173/review", "车源审核 行驶证信息") },
    {
      businessType: "vehicle_source",
      region: "default",
      profileVersion: "1.0",
      workflowStage: "vehicle_source",
      selectionMode: "AUTO",
      detectionStatus: "CONFIRMED",
    },
  );
  // 只命中一半不算。
  assert.equal(detect("http://localhost:5173/review", "车源审核"), null);
  assert.equal(detect("http://localhost:5173/review", "申请信息 报废车辆信息 报废证明编号"), null);
  assert.equal(detect("http://localhost:5173/review", "一致性审核"), null);
});

test("scrap replacement keeps using the address even when the page shows other words", () => {
  const { detect } = loadDetector();

  // 报废置换的地址各自唯一，不必看内容：地址够用时按内容判定反而会被共享的
  // 侧边栏、跨业务公共区块带偏。
  assert.equal(
    detect("https://admin.forjtruck.com/scrap-replace-qingdao", TRANSFER_PAGE_TEXT).businessType,
    "scrap_replacement",
  );
});

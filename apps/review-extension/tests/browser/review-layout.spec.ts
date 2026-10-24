import { expect, test, type Page } from "@playwright/test";

declare global {
  interface Window {
    reviewLayoutFixture: {
      actions: { type: string; imageId?: string; action?: { field: string; value: string } }[];
      finish: boolean;
      blocking: boolean;
    };
  }
}

async function start(page: Page, query = "") {
  await page.goto(`/tests/browser/review-layout.html${query}`);
  await expect(page.getByRole("button", { name: "开始审核检查", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "开始审核检查", exact: true }).click();
}

for (const business of ["vehicle_source", "transfer", "scrap_replacement"]) {
  for (const width of [320, 480, 1040]) {
    test(`${business} ${width}px 顶部五行、合并材料卡和首行回填`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.setViewportSize({ width, height: 1000 });
      await start(page, `?business=${business}`);
      const list = page.locator(".workbench-index"),
        detail = page.locator(".workbench-detail");
      await expect(list.locator("button")).toHaveCount(7);
      const geometry = await list.evaluate((element) => ({
        height: element.clientHeight,
        scroll: element.scrollHeight,
        row: element.firstElementChild!.getBoundingClientRect().height,
        gap: parseFloat(getComputedStyle(element).rowGap),
      }));
      expect(geometry.height).toBeCloseTo(geometry.row * 5 + geometry.gap * 4 + 4, 0);
      expect(geometry.scroll).toBeGreaterThan(geometry.height);
      const listBounds = await list.boundingBox(),
        detailBounds = await detail.boundingBox();
      expect(listBounds!.y + listBounds!.height).toBeLessThan(detailBounds!.y);
      const card = detail.locator(".candidate").first();
      await expect(card.locator("img")).toBeVisible();
      await expect(card.getByRole("button", { name: "查看原图", exact: true })).toBeVisible();
      const heading = card.locator(".material-value-heading"),
        fill = heading.locator("button");
      await expect(fill).toHaveText("回填此值");
      const labelBounds = await heading.locator("span").boundingBox(),
        fillBounds = await fill.boundingBox();
      expect(fillBounds!.x).toBeGreaterThan(labelBounds!.x + labelBounds!.width);
      expect(
        Math.abs(fillBounds!.y + fillBounds!.height / 2 - labelBounds!.y - labelBounds!.height / 2),
      ).toBeLessThan(2);
      const manual = detail.locator(".manual-fill input");
      await expect(manual).toBeVisible();
      await manual.fill("保留人工草稿");
      await card.getByRole("button", { name: "查看原图", exact: true }).click();
      await expect(manual).toHaveValue("保留人工草稿");
      await expect(detail.locator(".workbench-message")).toContainText("已定位到原图");
      await fill.click();
      await expect(detail.locator(".page-value")).toContainText(
        "DEMO1234567890123456789012345678901234567890",
      );
      await expect(detail.locator(".workbench-card-title > b")).toHaveText("冲突");
      await page.getByRole("button", { name: "下一项", exact: true }).click();
      await expect(list.locator('[aria-pressed="true"]')).toContainText("机动车所有人");
      await expect(detail.locator("h2")).toHaveText("机动车所有人");
      await page.getByRole("button", { name: "标记人工复核", exact: true }).click();
      await expect(page.getByRole("button", { name: "待处理 (6)", exact: true })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      expect(errors).toEqual([]);
    });
  }
}

test("短列表按内容收缩，业务入口可用且无配置管理入口", async ({ page }) => {
  await start(page, "?short");
  const list = page.locator(".workbench-index");
  await expect(list.locator("button")).toHaveCount(2);
  expect(await list.evaluate((element) => element.clientHeight)).toBeLessThan(110);
  await expect(page.locator("#business-selection")).toBeEnabled();
  await expect(page.locator(".panel-more")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "采集并打开配置工作台" })).toHaveCount(0);
});

test("连续切换时只滚动顶部项目列表，保留页面阅读位置", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 1000 });
  await start(page);
  const initialScroll = await page.evaluate(() => scrollY);
  for (let index = 0; index < 6; index++)
    await page.getByRole("button", { name: "下一项", exact: true }).click();
  const list = page.locator(".workbench-index");
  await expect(list.locator('[aria-pressed="true"]')).toContainText("开票日期");
  expect(await list.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  expect(await page.evaluate(() => scrollY)).toBe(initialScroll);
  const bounds = await list.boundingBox(),
    selected = await list.locator('[aria-pressed="true"]').boundingBox();
  expect(selected!.y).toBeGreaterThanOrEqual(bounds!.y);
  expect(selected!.y + selected!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height);
});

test("识别中的材料待确认，完成后汇总；超时材料保留原图入口", async ({ page }) => {
  await start(page, "?progress&partial");
  await expect(page.getByRole("progressbar")).toBeVisible();
  await expect(page.locator(".presence-summary")).toHaveText("部分材料待确认");
  await page.evaluate(() => {
    window.reviewLayoutFixture.finish = true;
  });
  await expect(page.locator(".review-progress-heading")).toContainText("审核部分完成");
  await expect(page.locator(".review-progress-failures")).toContainText("识别超时");
  await expect(page.locator(".review-progress-failures button")).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveCount(0);
});

test("页面失效后仍停止回填，提示重新采集", async ({ page }) => {
  await start(page);
  await page.evaluate(() => {
    window.reviewLayoutFixture.blocking = true;
  });
  await page.locator(".material-value-heading button").click();
  await expect(page.locator(".workbench-blocked")).toContainText("页面记录已变化");
  await expect(page.locator(".material-value-heading button")).toBeDisabled();
  await expect(page.locator(".manual-fill input")).toBeDisabled();
  await expect(page.locator(".workbench-blocked button")).toHaveText("重新采集并复核");
});

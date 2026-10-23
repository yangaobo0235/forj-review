import { expect, test } from "@playwright/test";
import type { ModelFillResult, ModelSearchInput } from "../../src/shared/contracts/vehicle-model";

declare global {
  interface Window {
    vehicleModelFixture: {
      queries: ModelSearchInput[];
      parentChanges: string[];
      result: ModelFillResult | null;
      autoFill: boolean;
      linkageFailed: boolean;
    };
  }
}

test.beforeEach(async ({ page }) => {
  await page.route("**/vehicle-model/config/detail", (route) =>
    route.fulfill({
      status: 200,
      json: { driveForm: "8X4", maxHp: "510", engineType: "BUSINESS42" },
    }),
  );
  await page.goto("/tests/browser/vehicle-model.html");
  await expect(page.locator("#picker")).toContainText("找到 1 条候选车型记录");
});

test("只回填车型，慢响应后的规格值完全由业务页面带入", async ({ page }) => {
  let release: () => void;
  const response = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/vehicle-model/config/detail", async (route) => {
    await response;
    await route.fulfill({
      status: 200,
      json: { driveForm: "8X4", maxHp: "510", engineType: "BUSINESS42" },
    });
  });
  await page.locator('#picker input[type="radio"]').check();
  const pending = page.waitForRequest("**/vehicle-model/config/detail");
  await page.getByRole("button", { name: "回填车型" }).click();
  await pending;
  // The assistant confirms only the model; the host may still be loading specifications.
  await expect(page.locator('[id="spec.engine_model"]')).toHaveValue("WRONG42");
  await expect.poll(() => page.evaluate(() => window.vehicleModelFixture.result?.ok)).toBe(true);
  release!();
  await expect(page.locator('[id="spec.drive_form"]')).toHaveValue("8X4");
  await expect(page.locator('[id="spec.max_hp"]')).toHaveValue("510");
  await expect(page.locator('[id="spec.engine_model"]')).toHaveValue("BUSINESS42");
});

test("固定品牌、车系及车辆类型，清空型号后仍限定页面范围", async ({ page }) => {
  const picker = page.locator("#picker");
  await expect(picker).toContainText("页面限定品牌：合成品牌");
  await expect(picker.locator('[role="combobox"], select')).toHaveCount(0);
  await expect(picker.locator("form input")).toHaveCount(2);
  await picker.locator("#model-announcement").fill("");
  await picker.locator("#model-engine").fill("");
  await picker.getByRole("button", { name: "搜索车型" }).click();
  const input = await page.evaluate(() => window.vehicleModelFixture.queries.at(-1)!);
  expect(input.announcementModel).toBe("");
  expect(input.engineModel).toBe("");
  expect(input.pageScope.seriesId).toBe("3");
  expect(input.pageScope).toEqual({
    brandId: "2",
    brandName: "合成品牌",
    seriesId: "3",
    seriesName: "合成车系",
    typeLevel: "牵引车",
  });
});

test("两个型号默认使用图片识别结果，手动修改后可恢复材料型号", async ({ page }) => {
  const picker = page.locator("#picker");
  await expect(page.locator('[id="vehicle.brand_model"]')).toHaveValue("错误牌WRONG100");
  await expect(page.locator('[id="spec.engine_model"]')).toHaveValue("WRONG42");
  await expect(picker.locator("#model-announcement")).toHaveValue("MODEL100");
  await expect(picker.locator("#model-engine")).toHaveValue("ENGINE42-E6");
  const initial = await page.evaluate(() => window.vehicleModelFixture.queries[0]!);
  expect(initial.announcementModel).toBe("MODEL100");
  expect(initial.engineModel).toBe("ENGINE42-E6");
  await picker.locator("#model-engine").fill("MANUAL42");
  await expect(picker).toContainText("使用手动输入条件");
  await picker.getByRole("button", { name: "恢复材料型号" }).click();
  await expect(picker.locator("#model-engine")).toHaveValue("ENGINE42-E6");
  await expect(picker).toContainText("找到 1 条候选车型记录");
  const restored = await page.evaluate(() => window.vehicleModelFixture.queries.at(-1)!);
  expect(restored).toEqual(initial);
});

test("车系未改变时保留原选择，不触发品牌或车系联动", async ({ page }) => {
  await page.locator('#picker input[type="radio"]').check();
  await page.getByRole("button", { name: "回填车型" }).click();
  await expect(page.locator('[id="vehicle.model"]')).toHaveValue("10");
  await expect(page.locator('[id="spec.max_hp"]')).toHaveValue("510");
  await expect(page.locator('[id="spec.brand"]')).toHaveValue("2");
  await expect(page.locator('[id="spec.series"]')).toHaveValue("3");
  await expect.poll(() => page.evaluate(() => window.vehicleModelFixture.result?.ok)).toBe(true);
  const result = await page.evaluate(() => window.vehicleModelFixture);
  expect(result.result?.ok).toBe(true);
  expect(result.parentChanges).toEqual([]);
  expect(result.result?.actions).toHaveLength(1);
});

test("业务页面车系改变后禁止回填旧候选，即使显示名称相同", async ({ page }) => {
  await page.locator('#picker input[type="radio"]').check();
  await page.locator('[id="spec.series"]').selectOption("4");
  await page.getByRole("button", { name: "回填车型" }).click();
  await expect
    .poll(() => page.evaluate(() => window.vehicleModelFixture.result?.code))
    .toBe("MODEL_PAGE_SCOPE_CHANGED");
  await expect(page.locator('[id="spec.max_hp"]')).toHaveValue("420");
});

test("业务页面没有详情请求时仍能核验车型，不接管规格字段", async ({ page }) => {
  await page.evaluate(() => {
    window.vehicleModelFixture.autoFill = false;
  });
  await page.locator('#picker input[type="radio"]').check();
  await page.getByRole("button", { name: "回填车型" }).click();
  await expect.poll(() => page.evaluate(() => window.vehicleModelFixture.result?.ok)).toBe(true);
  await expect(page.locator('[id="vehicle.model"]')).toHaveValue("10");
  await expect(page.locator('[id="spec.max_hp"]')).toHaveValue("420");
  await expect(page.locator('[id="spec.engine_model"]')).toHaveValue("WRONG42");
});

test("业务页面参数加载失败时只报告车型回填结果", async ({ page }) => {
  await page.route("**/vehicle-model/config/detail", (route) =>
    route.fulfill({ status: 500, body: "{}" }),
  );
  await page.locator('#picker input[type="radio"]').check();
  await page.getByRole("button", { name: "回填车型" }).click();
  await expect.poll(() => page.evaluate(() => window.vehicleModelFixture.linkageFailed)).toBe(true);
  await expect.poll(() => page.evaluate(() => window.vehicleModelFixture.result?.ok)).toBe(true);
  await expect(page.locator('[id="spec.engine_model"]')).toHaveValue("WRONG42");
  await expect(page.locator("#picker")).toContainText("请核对页面");
  expect(
    await page.evaluate(() =>
      window.vehicleModelFixture.result?.actions?.map((item) => item.field),
    ),
  ).toEqual(["vehicle.model"]);
});

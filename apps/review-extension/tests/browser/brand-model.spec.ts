import { expect, test } from "@playwright/test";

declare global {
  interface Window {
    brandModelFixture: { fills: string[]; images: string[] };
  }
}

for (const width of [320, 480]) {
  test(`${width}px 品牌型号结论完整展示，材料读值与原图默认展开`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/brand-model.html");
    const result = page.locator(".completion-result");
    await expect(result).toContainText("陕汽牌SX4259MD4TLQ1");
    await expect(page.locator(".completion-suffix")).toHaveText("Q1");
    await expect(result.locator(".completion-materials")).toBeVisible();
    await expect(page.locator(".completion-evidence")).toHaveCount(2);
    for (const img of await page.locator(".completion-evidence img").all()) {
      await expect(img).toBeVisible();
      await expect
        .poll(() => img.evaluate((node) => (node as HTMLImageElement).naturalWidth))
        .toBeGreaterThan(0);
    }
    await expect(page.getByRole("textbox", { name: "品牌型号人工输入值" })).toBeVisible();
    const bounds = await result.boundingBox(),
      materials = await page.locator(".completion-materials").boundingBox();
    expect(bounds!.y).toBeLessThan(materials!.y);
    expect(materials!.y + materials!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    // 型号占据整行，不再被标签列挤成单字符竖排。
    const code = await result.locator(":scope > strong").boundingBox();
    expect(code!.height).toBeLessThan(55);
    await page.getByRole("button", { name: "品牌型号回填材料值", exact: true }).click();
    await expect(page.locator(".page-value")).toContainText("陕汽牌SX4259MD4TLQ1");
    expect(await page.evaluate(() => window.brandModelFixture.fills)).toEqual([
      "陕汽牌SX4259MD4TLQ1",
    ]);
    await expect(page.locator(".workbench-card-title > b")).toHaveText("冲突");
    await page.getByRole("button", { name: "查看原图", exact: true }).first().click();
    expect(await page.evaluate(() => window.brandModelFixture.images)).toEqual(["license"]);
  });
}

test("材料型号冲突允许人工决定回填并保留机器冲突结论", async ({ page }) => {
  await page.goto("/tests/browser/brand-model.html?scenario=conflict");
  await expect(page.getByRole("button", { name: "品牌型号回填材料值" })).toBeEnabled();
  await expect(page.locator(".completion-warning")).toContainText("整车型号不一致");
  await expect(page.locator(".completion-evidence img")).toHaveCount(2);
  await page.getByRole("textbox", { name: "品牌型号人工输入值" }).fill("陕汽牌SX4259MD4TLQ1");
  await page.locator(".brand-manual button").click();
  expect(await page.evaluate(() => window.brandModelFixture.fills)).toEqual([
    "陕汽牌SX4259MD4TLQ1",
  ]);
  await expect(page.locator(".workbench-card-title > b")).toHaveText("冲突");
  await expect(page.locator(".completion-warning")).toContainText("整车型号不一致");
});

test("页面完整一致时无需回填", async ({ page }) => {
  await page.goto("/tests/browser/brand-model.html?scenario=match");
  await expect(page.locator(".workbench-card-title > b")).toHaveText("一致");
  await expect(page.getByRole("button", { name: "品牌型号回填材料值" })).toBeDisabled();
  await expect(page.locator(".completion-note").last()).toContainText("页面已一致，无需回填");
});

import {expect, test} from "@playwright/test";

declare global {
  interface Window {
    brandModelFixture: {fills: string[]; images: string[]};
  }
}

for (const width of [320, 480]) {
  test(`${width}px 品牌型号结论完整展示，材料读值与原图默认展开`, async ({page}) => {
    await page.setViewportSize({width, height: 1000});
    await page.goto("/tests/browser/brand-model.html");
    const result = page.locator(".completion-result");
    await expect(result).toContainText("陕汽牌SX4259MD4TLQ1");
    await expect(page.locator(".completion-suffix")).toHaveText("Q1");
    await expect(page.locator(".completion-materials")).toHaveAttribute("open", "");
    await expect(page.locator(".completion-evidence")).toHaveCount(2);
    for (const img of await page.locator(".completion-evidence img").all()) {
      await expect(img).toBeVisible();
      await expect.poll(() => img.evaluate(node => (node as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    }
    await expect(page.getByRole("textbox", {name: "品牌型号人工输入值"})).toBeVisible();
    const bounds = await result.boundingBox(), materials = await page.locator(".completion-materials").boundingBox();
    expect(bounds!.y + bounds!.height).toBeLessThan(materials!.y);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    // 型号占据整行，不再被标签列挤成单字符竖排。
    const code = await result.locator("strong").boundingBox();
    expect(code!.height).toBeLessThan(55);
    await page.getByRole("button", {name: "回填完整品牌型号", exact: true}).click();
    await expect(page.locator(".page-value")).toContainText("陕汽牌SX4259MD4TLQ1");
    expect(await page.evaluate(() => window.brandModelFixture.fills)).toEqual(["陕汽牌SX4259MD4TLQ1"]);
    await expect(page.locator(".workbench-card-title > b")).toHaveText("冲突");
    await page.getByRole("button", {name: "查看原图", exact: true}).first().click();
    expect(await page.evaluate(() => window.brandModelFixture.images)).toEqual(["license"]);
  });
}

test("材料冲突保留待复核与禁用原因，人工回填不改变机器结论", async ({page}) => {
  await page.goto("/tests/browser/brand-model.html?scenario=blocked");
  await expect(page.getByRole("button", {name: "材料待核对，暂不可自动回填"})).toBeDisabled();
  await expect(page.locator(".completion-warning")).toContainText("VIN不一致");
  await expect(page.locator(".completion-evidence img")).toHaveCount(2);
  await page.getByRole("textbox", {name: "品牌型号人工输入值"}).fill("陕汽牌SX4259MD4TLQ1");
  await page.locator(".brand-manual button").click();
  expect(await page.evaluate(() => window.brandModelFixture.fills)).toEqual(["陕汽牌SX4259MD4TLQ1"]);
  await expect(page.locator(".workbench-card-title > b")).toHaveText("待复核");
  await expect(page.locator(".completion-warning")).toContainText("VIN不一致");
});

test("页面完整一致时无需回填", async ({page}) => {
  await page.goto("/tests/browser/brand-model.html?scenario=match");
  await expect(page.locator(".workbench-card-title > b")).toHaveText("一致");
  await expect(page.getByRole("button", {name: "页面已一致，无需回填"})).toBeDisabled();
});

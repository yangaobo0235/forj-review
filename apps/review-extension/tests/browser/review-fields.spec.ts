import { expect, test } from "@playwright/test";

declare global {
  interface Window {
    reviewFixture: {
      collect(): {pageFields: Record<string, string>; reviewFields: unknown[]; ambiguousFields: string[]};
      fill(field: string, value: string, expectedValue: string): Promise<{ok: boolean}>;
    };
  }
}

test.beforeEach(async ({page}) => {
  await page.goto("/tests/browser/fixture.html");
  await page.waitForFunction(() => Boolean(window.reviewFixture));
});

test("浏览器中同名发票字段按分区采集，空值仍保留回填目标", async ({page}) => {
  await page.locator('[id="transfer_invoice.invoice_no"]').fill("");
  const result = await page.evaluate(() => {
    const collected = window.reviewFixture.collect();
    return {fields: collected.pageFields, count: collected.reviewFields.length, ambiguous: collected.ambiguousFields};
  });
  expect(result.count).toBe(17);
  expect(result.ambiguous).toEqual([]);
  expect(result.fields["transfer_invoice.invoice_no"]).toBe("");
  expect(result.fields["transfer_new_invoice.invoice_no"]).toBe("transfer_new_invoice.invoice_no-原值");
});

test("回填只更新选定发票且拒绝覆盖后来修改的值", async ({page}) => {
  const first = await page.evaluate(() => window.reviewFixture.fill("transfer_invoice.invoice_no", "00123456789012345678", "transfer_invoice.invoice_no-原值"));
  expect(first.ok).toBe(true);
  await expect(page.locator('[id="transfer_invoice.invoice_no"]')).toHaveValue("00123456789012345678");
  await expect(page.locator('[id="transfer_new_invoice.invoice_no"]')).toHaveValue("transfer_new_invoice.invoice_no-原值");
  const stale = await page.evaluate(() => window.reviewFixture.fill("transfer_invoice.invoice_no", "不得覆盖", "transfer_invoice.invoice_no-原值"));
  expect(stale.ok).toBe(false);
  await expect(page.locator('[id="transfer_invoice.invoice_no"]')).toHaveValue("00123456789012345678");
});

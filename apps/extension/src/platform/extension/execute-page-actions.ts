import { PageActionRegistry } from "./page-action-registry";
import { verifyInvoice } from "./page-fill-client";
import type { PageActionIntent, PageData } from "../../shared/contracts/review";

export async function executePageActions(
  actions: readonly PageActionIntent[],
  data: PageData,
): Promise<string[]> {
  const registry = new PageActionRegistry();
  registry.register("verify_invoice", async (action, page) => {
    const field = action.payload.field;
    if (
      typeof field !== "string" ||
      typeof page.pageFields[field] !== "string"
    ) {
      return "未取得发票号码的页面原值，请在原页面人工验真";
    }
    const verification = await verifyInvoice({
      field,
      expectedValue: page.pageFields[field],
      tabId: page.sourceTabId,
      pageUrl: page.pageUrl,
      pageInstanceId: page.pageInstanceId,
      pageFingerprint: page.pageFingerprint,
      collectionId: page.collectionId,
    });
    return verification.ok
      ? "发票号码核验通过，已点击一键验真"
      : verification.message || "发票号码已通过比对，但未找到一键验真按钮";
  });
  return registry.executeAll(actions, data);
}

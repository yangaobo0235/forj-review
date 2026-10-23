import { PageActionRegistry } from "./page-action-registry";
import { setAffiliation, verifyInvoice } from "./page-action-client";
import type { PageActionIntent, PageData } from "../../shared/contracts/review";

export async function executePageActions(
  actions: readonly PageActionIntent[],
  data: PageData,
): Promise<string[]> {
  const registry = new PageActionRegistry();
  registry.register("verify_invoice", async (action, page) => {
    const field = action.payload.field;
    if (typeof field !== "string" || typeof page.pageFields[field] !== "string") {
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
  registry.register("set_affiliation", async (action, page) => {
    const value = action.payload.value;
    if (value !== "公司" && value !== "个人") {
      return "挂靠回填值缺少明确的主体材料依据，请人工处理";
    }
    const result = await setAffiliation({
      tabId: page.sourceTabId,
      pageUrl: page.pageUrl,
      pageInstanceId: page.pageInstanceId,
      pageFingerprint: page.pageFingerprint,
      collectionId: page.collectionId,
      value,
    });
    return result.ok
      ? `报废车挂靠和新车挂靠已自动设置为${value}`
      : result.message || `两个挂靠字段未能自动设置为${value}，请人工处理`;
  });
  return registry.executeAll(actions, data);
}

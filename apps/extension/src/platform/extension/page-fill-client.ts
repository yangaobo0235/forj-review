import type { PageWriteAction, PageWriteGroupAction } from "../../shared/contracts/review";

import type { PageFillResult } from "../../shared/contracts/page-fill.ts";
export type { PageFillResult } from "../../shared/contracts/page-fill.ts";
export { isBlockingPageFillFailure } from "../../shared/contracts/page-fill.ts";

interface ChromeTabsLike {
  tabs: {
    sendMessage?(tabId: number, message: unknown): Promise<PageFillResult>;
  };
}

export interface PageFillTarget {
  tabId: number;
  pageUrl: string;
  pageInstanceId: string;
  pageFingerprint: string;
  collectionId: string;
}

export async function applyPageFieldValue(
  action: PageWriteAction,
  target: PageFillTarget,
  chromeApi: ChromeTabsLike = globalThis.chrome,
): Promise<PageFillResult> {
  if (!action.field || !action.value) return { ok: false, message: "回填值不能为空" };
  if (!Number.isInteger(target.tabId) || !target.pageUrl || !target.pageInstanceId || !target.pageFingerprint || !target.collectionId || !chromeApi.tabs.sendMessage) {
    return { ok: false, message: "原审核页面标识不完整，请重新审核" };
  }
  return chromeApi.tabs.sendMessage(target.tabId, {
    type: "APPLY_PAGE_FIELD_VALUE",
    action,
    expectedPageUrl: target.pageUrl,
    expectedPageInstanceId: target.pageInstanceId,
    expectedPageFingerprint: target.pageFingerprint,
    expectedCollectionId: target.collectionId,
  });
}

export async function applyPageFieldGroupValue(
  action: PageWriteGroupAction,
  target: PageFillTarget,
  chromeApi: ChromeTabsLike = globalThis.chrome,
): Promise<PageFillResult> {
  if (action.fields.length < 2 || new Set(action.fields).size !== action.fields.length || !action.value) {
    return { ok: false, message: "组合字段回填参数不完整" };
  }
  if (!Number.isInteger(target.tabId) || !target.pageUrl || !target.pageInstanceId || !target.pageFingerprint || !target.collectionId || !chromeApi.tabs.sendMessage) {
    return { ok: false, message: "原审核页面标识不完整，请重新审核" };
  }
  return chromeApi.tabs.sendMessage(target.tabId, {
    type: "APPLY_PAGE_FIELD_GROUP_VALUE",
    action,
    expectedPageUrl: target.pageUrl,
    expectedPageInstanceId: target.pageInstanceId,
    expectedPageFingerprint: target.pageFingerprint,
    expectedCollectionId: target.collectionId,
  });
}

export async function verifyInvoice(
  target: PageFillTarget & { field: string; expectedValue: string },
  chromeApi: ChromeTabsLike = globalThis.chrome,
): Promise<PageFillResult> {
  if (!Number.isInteger(target.tabId) || !target.pageUrl || !target.pageInstanceId || !target.pageFingerprint || !target.collectionId || !chromeApi.tabs.sendMessage) {
    return { ok: false, message: "原审核页面标识不完整，请重新审核" };
  }
  return chromeApi.tabs.sendMessage(target.tabId, {
    type: "VERIFY_INVOICE",
    action: { field: target.field, expectedValue: target.expectedValue },
    expectedPageUrl: target.pageUrl,
    expectedPageInstanceId: target.pageInstanceId,
    expectedPageFingerprint: target.pageFingerprint,
    expectedCollectionId: target.collectionId,
  });
}

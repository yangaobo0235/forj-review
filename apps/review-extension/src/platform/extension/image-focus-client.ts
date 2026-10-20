export interface ImageFocusResult {
  ok: boolean;
  error?: string;
}

export interface ImageFocusTarget {
  sourceTabId: number;
  pageUrl: string;
  pageInstanceId: string;
  pageFingerprint: string;
  collectionId: string;
}

interface ChromeTabsLike {
  tabs: {
    sendMessage?(tabId: number, message: unknown): Promise<ImageFocusResult>;
  };
}

export async function focusReviewImage(
  imageId: string,
  target: ImageFocusTarget,
  chromeApi: ChromeTabsLike = globalThis.chrome,
): Promise<ImageFocusResult> {
  if (!imageId || !Number.isInteger(target.sourceTabId) || !target.pageUrl || !target.pageInstanceId || !target.pageFingerprint || !target.collectionId || !chromeApi.tabs.sendMessage) {
    return { ok: false, error: "原审核页面标识不完整，请重新采集" };
  }
  // 发送失败（标签页已关闭、内容脚本未注入）会让 Promise 直接拒绝。不接住的话
  // 调用方只看到"点了没反应"，连一句提示都没有——原图定位本来就可能失败，
  // 失败原因必须回到界面上。
  try {
    return await chromeApi.tabs.sendMessage(target.sourceTabId, {
      type: "FOCUS_REVIEW_IMAGE",
      imageId,
      expectedPageUrl: target.pageUrl,
      expectedPageInstanceId: target.pageInstanceId,
      expectedPageFingerprint: target.pageFingerprint,
      expectedCollectionId: target.collectionId,
    });
  } catch (error) {
    return {
      ok: false,
      error: `无法连接原审核页面，请刷新该页面后重新采集（${error instanceof Error ? error.message : "发送失败"}）`,
    };
  }
}

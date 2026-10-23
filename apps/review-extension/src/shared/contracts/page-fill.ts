export interface PageFillResult {
  ok: boolean;
  skipped?: boolean;
  message: string;
  code?: string;
  currentValue?: string;
  actions?: Array<{ field: string; label: string; value: string; status: string }>;
}

/** Field errors remain recoverable; only a lost page identity blocks the session. */
export function isBlockingPageFillFailure(
  result: Pick<PageFillResult, "message" | "code">,
): boolean {
  if (result.code) return result.code === "PAGE_IDENTITY_CHANGED";
  return /页面已变化|原审核页面标识不完整|context invalidated|Receiving end/i.test(result.message);
}

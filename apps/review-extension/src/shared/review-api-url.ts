export const localReviewApiBaseUrl = "http://127.0.0.1:26101";
export const publicReviewApiBaseUrl = "http://175.178.6.214:26101";

declare const __REVIEW_BUILD_MODE__: string | undefined;
declare const __REVIEW_API_BASE_URL__: string | undefined;

/** 根据构建模式选择审核服务地址；显式 VITE_REVIEW_API_BASE_URL 可覆盖预设地址。 */
export function resolveReviewApiBaseUrl(
  mode: string | undefined,
  configuredUrl: string | undefined,
): string {
  const normalizedUrl = configuredUrl?.trim().replace(/\/+$/, "");
  if (normalizedUrl) return normalizedUrl;
  return mode === "public" ? publicReviewApiBaseUrl : localReviewApiBaseUrl;
}

export const reviewApiBaseUrl = resolveReviewApiBaseUrl(
  typeof __REVIEW_BUILD_MODE__ === "string"
    ? __REVIEW_BUILD_MODE__
    : undefined,
  typeof __REVIEW_API_BASE_URL__ === "string"
    ? __REVIEW_API_BASE_URL__
    : undefined,
);


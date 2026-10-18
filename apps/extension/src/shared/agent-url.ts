export const localAgentBaseUrl = "http://127.0.0.1:8010";
export const publicAgentBaseUrl = "http://175.178.6.214:18110";

declare const __REVIEW_AGENT_BUILD_MODE__: string | undefined;
declare const __REVIEW_AGENT_BASE_URL__: string | undefined;

/** 根据构建模式选择 Agent；显式 VITE_AGENT_BASE_URL 可覆盖预设地址。 */
export function resolveAgentBaseUrl(
  mode: string | undefined,
  configuredUrl: string | undefined,
): string {
  const normalizedUrl = configuredUrl?.trim().replace(/\/+$/, "");
  if (normalizedUrl) return normalizedUrl;
  return mode === "public" ? publicAgentBaseUrl : localAgentBaseUrl;
}

export const agentBaseUrl = resolveAgentBaseUrl(
  typeof __REVIEW_AGENT_BUILD_MODE__ === "string"
    ? __REVIEW_AGENT_BUILD_MODE__
    : undefined,
  typeof __REVIEW_AGENT_BASE_URL__ === "string"
    ? __REVIEW_AGENT_BASE_URL__
    : undefined,
);


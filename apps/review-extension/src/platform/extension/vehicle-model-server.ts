import { reviewApiBaseUrl } from "../../shared/review-api-url.ts";
import type { ModelSearchInput } from "../../shared/contracts/vehicle-model";

/** 扩展后台访问审核助手，避开业务页 CORS；不读取业务系统 token。 */
export async function requestModelCatalog(input: ModelSearchInput, fetcher: typeof fetch = fetch) {
  const response = await fetcher(`${reviewApiBaseUrl}/api/review/vehicle-models/search`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(input), signal: AbortSignal.timeout(20000),
  });
  const body = await response.json();
  if (!response.ok) return {ok: false, message: typeof body?.detail === "string" ? body.detail : "车型查询失败，请检查搜索条件和审核服务"};
  return {ok: true, candidates: body};
}

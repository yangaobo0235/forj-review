import { reviewApiBaseUrl } from "../../shared/review-api-url.ts";
import type { ModelSearchInput } from "../../shared/contracts/vehicle-model";

/** 扩展后台访问审核助手，避开业务页 CORS；不读取业务系统 token。 */
export async function requestModelCatalog(input: ModelSearchInput, fetcher: typeof fetch = fetch) {
  const response = await fetcher(`${reviewApiBaseUrl}/api/review/vehicle-models/search`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(input), signal: AbortSignal.timeout(20000),
  });
  const body = await response.json();
  if (!response.ok) return {ok: false, message: typeof body?.message === "string" ? body.message : typeof body?.detail === "string" ? body.detail : "车型查询失败，请检查搜索条件和审核服务"};
  const seen = new Set<string>();
  if (!Array.isArray(body?.candidates) || body.candidates.some((item: unknown) => {
    if (!item || typeof item !== "object") return true;
    const value = item as Record<string, unknown>;
    if (typeof value.modelId !== "string" || !/^[0-9]{1,19}$/.test(value.modelId)
      || BigInt(value.modelId) <= 0n || BigInt(value.modelId) > 9223372036854775807n
      || seen.has(value.modelId) || typeof value.modelName !== "string" || !value.modelName.trim()) return true;
    seen.add(value.modelId);
    if (!value.config || typeof value.config !== "object") return true;
    const config = value.config as Record<string, unknown>;
    return ["driveForm", "fuelType", "emissionStandard", "maxHp", "engineBrand", "engineType"]
      .some(key => config[key] !== null && typeof config[key] !== "string");
  })) return {ok: false, message: "车型查询响应不完整或车型 ID 重复，请重试"};
  return {ok: true, candidates: body.candidates};
}

import type { PageData } from "../../shared/contracts/review";
import type { ModelFillResult, ModelSearchInput, ModelSearchResult, VehicleModelCandidate } from "../../shared/contracts/vehicle-model";
import { validatedModelInput } from "../../shared/vehicle-model.ts";

/** 两段扩展通信以及服务错误均转换为可操作的提示，避免显示浏览器内部英文。 */
export function vehicleModelMessage(reason: unknown): string {
  const message = typeof reason === "string" ? reason
    : reason && typeof reason === "object" && "message" in reason ? String(reason.message) : "";
  if (/receiving end|could not establish connection|context invalidated|message (?:port|channel).*closed|no (?:tab|document) with id/i.test(message)) {
    return "页面或扩展连接已失效，请刷新业务页面并重新读取后重试";
  }
  if (/failed to fetch|networkerror|network error|fetch failed|timeout|timed out|aborted/i.test(message)) {
    return "审核服务连接失败或查询超时，请检查服务后重试";
  }
  return /[\u3400-\u9fff]/.test(message) ? message : "车型操作失败，请检查页面连接和审核服务后重试";
}

function identity(page: PageData) {
  return { expectedPageUrl: page.pageUrl, expectedPageInstanceId: page.pageInstanceId,
    expectedPageFingerprint: page.pageFingerprint, expectedCollectionId: page.collectionId };
}
export function queryVehicleModels(page: PageData, input: ModelSearchInput): Promise<ModelSearchResult> {
  return chrome.runtime.sendMessage({ type: "SEARCH_REVIEW_VEHICLE_MODELS", sourceTabId: page.sourceTabId, input, ...identity(page) });
}
export function fillVehicleModel(page: PageData, searchId: string, modelId: string): Promise<ModelFillResult> {
  return chrome.tabs.sendMessage(page.sourceTabId, { type: "APPLY_VEHICLE_MODEL", searchId, modelId, ...identity(page) });
}

/** content 回填前重新查询同一离线目录，拒绝页面变更期间返回的配置。 */
export async function searchVehicleModels(input: ModelSearchInput, guard: () => boolean, expectedPageUrl: string): Promise<VehicleModelCandidate[]> {
  const query = validatedModelInput(input);
  if (!guard()) throw new Error("页面已变化，请重新采集");
  const result = await chrome.runtime.sendMessage({type: "QUERY_VEHICLE_MODEL_CATALOG", input: query, expectedPageUrl});
  if (!guard()) throw new Error("页面已变化，请重新采集");
  if (!result?.ok || !Array.isArray(result.candidates)) throw new Error(result?.message || "车型查询失败，请稍后重试");
  return result.candidates;
}

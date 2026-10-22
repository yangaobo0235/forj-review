/** 车型领域规则：条件规范化、页面范围与规格映射；不访问 DOM、Chrome 或网络。 */
import type { ModelPageSelection, ModelPageScope, ModelSearchInput, SpecFillValue, VehicleModelCandidate } from "./contracts/vehicle-model";

const string = (value: unknown) => value == null ? "" : String(value).trim();
export const normalizeModelCode = (value: unknown) => string(value).normalize("NFKC").replace(/\s+/g, "").toUpperCase();
// 只去除品牌文字前缀；型号中的标点、数字及后缀全部保留。
export const normalizeEngineCode = (value: unknown) => normalizeModelCode(value).replace(/^[\u3400-\u9fff]+/, "");
export function modelPageScope(selection?: ModelPageSelection): ModelPageScope {
  return {brandId: string(selection?.brand?.id), brandName: string(selection?.brand?.label), seriesId: string(selection?.series?.id), seriesName: string(selection?.series?.label), typeLevel: string(selection?.vehicleType)};
}
export function modelScopeIssue(scope: ModelPageScope): string {
  if (!/^[0-9]{1,19}$/.test(scope.brandId) || !isModelId(scope.brandId)) {
    return "请在业务页面选择品牌后重新采集；必须读取真实选项 ID";
  }
  if (!/^[0-9]{1,19}$/.test(scope.seriesId) || !isModelId(scope.seriesId)) return "请在业务页面选择车系后重新采集；必须读取真实业务车系 ID";
  if (!scope.typeLevel || scope.typeLevel.length > 40) return "请在业务页面车源基础信息中选择车辆类型后重新采集";
  return "";
}
const isModelId = (value: string) => BigInt(value) > 0n && BigInt(value) <= 9223372036854775807n;
export function validatedModelInput(input: ModelSearchInput): ModelSearchInput {
  if (Object.keys(input || {}).some(key => !["announcementModel", "engineModel", "pageScope"].includes(key))) throw new Error("车型查询仅接受页面固定范围及两个型号条件");
  const announcementModel = normalizeModelCode(input?.announcementModel);
  const engineModel = normalizeEngineCode(input?.engineModel);
  if (![announcementModel, engineModel].every(value => !value || /^[A-Z0-9][A-Z0-9.-]{1,79}$/.test(value))) {
    throw new Error("请输入完整型号，保留型号后缀；不使用的条件可留空");
  }
  const pageScope = {brandId: string(input?.pageScope?.brandId), brandName: string(input?.pageScope?.brandName), seriesId: string(input?.pageScope?.seriesId), seriesName: string(input?.pageScope?.seriesName), typeLevel: string(input?.pageScope?.typeLevel)};
  const issue = modelScopeIssue(pageScope);
  if (issue) throw new Error(issue);
  return { announcementModel, engineModel, pageScope };
}

/** The backend version owns mappings; this function only executes registered converters. */
export function modelFillPlan(candidate: VehicleModelCandidate, current: Record<string, unknown> = {},
  mappings: readonly import("@forj/configuration-contracts").ActionMapping[] = [], labels: Record<string, string> = {}, selection?: ModelPageSelection): { values: SpecFillValue[]; missing: string[]; warnings: string[] } {
  const values: SpecFillValue[] = [], missing: string[] = [], warnings: string[] = [];
  if (!mappings.length) return {values, missing: ["当前页面未配置车型回填映射"], warnings};
  const property = (path: string) => path.split(".").reduce<unknown>((value, key) =>
    value && typeof value === "object" && Object.hasOwn(value, key) ? (value as Record<string, unknown>)[key] : undefined, candidate);
  const completed = new Set<string>();
  for (const mapping of [...mappings].sort((a, b) => a.order - b.order)) {
    if (mapping.source_kind !== "MODEL_CANDIDATE" || mapping.depends_on.some(id => !completed.has(id))) {
      missing.push("车型回填映射依赖无效"); continue;
    }
    const fixed = mapping.target_field === "spec.brand" && mapping.source_property === "brandName" && mapping.source_option_property === "brandId" ? selection?.brand
      : mapping.target_field === "spec.series" && mapping.source_property === "seriesName" && mapping.source_option_property === "seriesId" ? selection?.series : undefined;
    // Published v1 actions include scope mappings. They now express preconditions,
    // preserving the original dependency chain without writing fixed page selections.
    if (fixed) {
      if (!fixed.id || !fixed.label) missing.push(labels[mapping.target_field] || mapping.target_field);
      completed.add(mapping.mapping_id); continue;
    }
    let value = string(property(mapping.source_property));
    let label = labels[mapping.target_field] || mapping.target_field;
    if (["--", "null", "undefined"].includes(value)) value = "";
    if (mapping.converter_id === "positive_number" && (!Number.isFinite(Number(value)) || Number(value) <= 0)) value = "";
    if (!value && mapping.missing_policy === "RESTORE_ORIGINAL") {
      value = string(current[mapping.target_field]);
      warnings.push(value ? `车型库未提供${label}，联动后保留页面原值` : `车型库未提供${label}，本次不改写该字段`);
      if (value) label += "（保留原值）";
    }
    if (!value && mapping.missing_policy === "BLOCK") missing.push(label);
    const selected = mapping.source_option_property === "brandId" ? selection?.brand
      : mapping.source_option_property === "seriesId" ? selection?.series
        : mapping.source_option_property === "modelId" ? selection?.model : undefined;
    if (value && selected?.id === string(property(mapping.source_option_property || "")) && selected.label) value = selected.label;
    if (value) values.push({field: mapping.target_field, label, value,
      ...(mapping.source_option_property ? {optionValue: string(property(mapping.source_option_property))} : {})});
    completed.add(mapping.mapping_id);
  }
  return {values, missing, warnings};
}

/** 同名记录分别保留；写回器必须按 optionValue 选择并核验真实 ID。 */
export function verifyModelOption(candidate: VehicleModelCandidate) {
  if (!/^[0-9]{1,19}$/.test(candidate.modelId) || !isModelId(candidate.modelId)) throw new Error("车型缺少有效车型 ID，无法回填");
}

/** 消息和 Chrome storage 的对象属性顺序不属于查询/配置身份。 */
export function sameModelData(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right)
    && left.length === right.length && left.every((item, index) => sameModelData(item, right[index]));
  const a = left as Record<string, unknown>, b = right as Record<string, unknown>;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every(key => Object.hasOwn(b, key) && sameModelData(a[key], b[key]));
}

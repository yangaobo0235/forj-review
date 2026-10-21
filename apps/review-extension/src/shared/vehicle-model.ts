/** 车型领域规则：条件规范化、页面范围与规格映射；不访问 DOM、Chrome 或网络。 */
import type { ModelPageScope, ModelSearchInput, SpecFillValue, VehicleModelCandidate } from "./contracts/vehicle-model";

const string = (value: unknown) => value == null ? "" : String(value).trim();
export const normalizeModelCode = (value: unknown) => string(value).normalize("NFKC").replace(/\s+/g, "").toUpperCase();
// 只去除品牌文字前缀；型号中的标点、数字及后缀全部保留。
export const normalizeEngineCode = (value: unknown) => normalizeModelCode(value).replace(/^[\u3400-\u9fff]+/, "");
export function modelPageScope(fields: Record<string, unknown> = {}) {
  return {brandName: string(fields["spec.brand"]), seriesName: string(fields["spec.series"]),
    typeLevel: string(fields["vehicle.type"])};
}
export function modelScopeIssue(scope: ModelPageScope): string {
  if (!scope.brandName || !scope.seriesName || !scope.typeLevel) return "缺少页面品牌、车系或车辆类型，请先选择完整并重新读取页面";
  if ([scope.brandName, scope.seriesName].some(value => /^\d+$/.test(value))) {
    return "页面品牌或车系只读到了内部编号，未读到选中名称，请刷新业务页面并重新读取";
  }
  return "";
}
export function validatedModelInput(input: ModelSearchInput): ModelSearchInput {
  const announcementModel = normalizeModelCode(input?.announcementModel);
  const engineModel = normalizeEngineCode(input?.engineModel);
  if (![announcementModel, engineModel].every(value => /^[A-Z0-9][A-Z0-9.-]{1,79}$/.test(value))) {
    throw new Error("请输入完整整车型号和发动机型号，保留型号后缀");
  }
  const pageScope = {brandName: string(input.pageScope?.brandName), seriesName: string(input.pageScope?.seriesName),
    typeLevel: string(input.pageScope?.typeLevel)};
  const issue = modelScopeIssue(pageScope);
  if (issue) throw new Error(issue);
  return { announcementModel, engineModel, pageScope };
}

/** The backend version owns mappings; this function only executes registered converters. */
export function modelFillPlan(candidate: VehicleModelCandidate, current: Record<string, unknown> = {},
  mappings: readonly import("@forj/configuration-contracts").ActionMapping[] = [], labels: Record<string, string> = {}): { values: SpecFillValue[]; missing: string[]; warnings: string[] } {
  const values: SpecFillValue[] = [], missing: string[] = [], warnings = [...candidate.warnings];
  if (!mappings.length) return {values, missing: ["当前页面未配置车型回填映射"], warnings};
  const property = (path: string) => path.split(".").reduce<unknown>((value, key) =>
    value && typeof value === "object" && Object.hasOwn(value, key) ? (value as Record<string, unknown>)[key] : undefined, candidate);
  const completed = new Set<string>();
  for (const mapping of [...mappings].sort((a, b) => a.order - b.order)) {
    if (mapping.source_kind !== "MODEL_CANDIDATE" || mapping.depends_on.some(id => !completed.has(id))) {
      missing.push("车型回填映射依赖无效"); continue;
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
    if (value) values.push({field: mapping.target_field, label, value,
      ...(mapping.source_option_property ? {optionValue: string(property(mapping.source_option_property))} : {})});
    completed.add(mapping.mapping_id);
  }
  return {values, missing, warnings};
}

/** 同名记录分别保留；写回器必须按 optionValue 选择并核验真实 ID。 */
export function verifyModelOption(candidate: VehicleModelCandidate) {
  if (![candidate.modelId, candidate.brandId, candidate.seriesId].every(value => /^\d+$/.test(value))) throw new Error("车型缺少有效品牌、车系或车型ID，无法回填");
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

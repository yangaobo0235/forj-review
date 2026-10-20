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

const SPEC_CONFIG_FIELDS = [
  ["spec.drive_form", "驱动形式", "driveForm"], ["spec.fuel_type", "燃料种类", "fuelType"],
  ["spec.emission_standard", "排放标准", "emissionStandard"], ["spec.max_hp", "最大马力", "maxHp"],
  ["spec.engine_brand", "发动机品牌", "engineBrand"], ["spec.engine_model", "发动机型号", "engineType"],
] as const;
export function modelFillPlan(candidate: VehicleModelCandidate, current: Record<string, unknown> = {}): { values: SpecFillValue[]; missing: string[]; warnings: string[] } {
  const values: SpecFillValue[] = [
    { field: "spec.brand", label: "品牌", value: candidate.brandName, optionValue: candidate.brandId },
    { field: "spec.series", label: "车系", value: candidate.seriesName, optionValue: candidate.seriesId },
    { field: "vehicle.model", label: "车型", value: candidate.modelName, optionValue: candidate.modelId },
    ...SPEC_CONFIG_FIELDS.map(([field, label, key]) => ({ field, label, value: string(candidate.config[key]) })),
  ];
  const cargo = string(candidate.config.packingCaseLength);
  const hasCargo = Boolean(cargo && Number.isFinite(Number(cargo)) && Number(cargo) > 0);
  if (hasCargo) values.push({ field: "spec.cargo_length", label: "货箱长度", value: cargo });
  // 选择车型会重建整块表单；显式恢复原有非空值，避免联动把可选货箱长度清掉。
  const originalCargo = string(current["spec.cargo_length"]);
  if (!hasCargo && originalCargo) values.push({field: "spec.cargo_length", label: "货箱长度（保留原值）", value: originalCargo});
  const missing = values.filter(item => !item.value || ["--", "null", "undefined"].includes(item.value)).map(item => item.label);
  return { values, missing, warnings: [...candidate.warnings, ...(!hasCargo
    ? [originalCargo ? "车型库未提供货箱长度，联动后保留页面原值" : "车型库未提供货箱长度，本次不改写该字段"] : [])] };
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

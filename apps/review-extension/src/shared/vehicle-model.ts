/** 车型领域规则：条件规范化、页面范围与车型映射；不访问 DOM、Chrome 或网络。 */
import type {
  ModelPageSelection,
  ModelPageScope,
  ModelSearchInput,
  ModelFillValue,
  VehicleModelCandidate,
} from "./contracts/vehicle-model";

const string = (value: unknown) => (value == null ? "" : String(value).trim());
export const normalizeModelCode = (value: unknown) =>
  string(value).normalize("NFKC").replace(/\s+/g, "").toUpperCase();
// 只去除品牌文字前缀；型号中的标点、数字及后缀全部保留。
export const normalizeEngineCode = (value: unknown) =>
  normalizeModelCode(value).replace(/^[\u3400-\u9fff]+/, "");
export function modelPageScope(selection?: ModelPageSelection): ModelPageScope {
  return {
    brandId: string(selection?.brand?.id),
    brandName: string(selection?.brand?.label),
    seriesId: string(selection?.series?.id),
    seriesName: string(selection?.series?.label),
    typeLevel: string(selection?.vehicleType),
  };
}
export function modelScopeIssue(scope: ModelPageScope): string {
  if (!/^[0-9]{1,19}$/.test(scope.brandId) || !isModelId(scope.brandId)) {
    return "请在业务页面选择品牌后重新采集；必须读取真实选项 ID";
  }
  if (!/^[0-9]{1,19}$/.test(scope.seriesId) || !isModelId(scope.seriesId))
    return "请在业务页面选择车系后重新采集；必须读取真实业务车系 ID";
  if (!scope.typeLevel || scope.typeLevel.length > 40)
    return "请在业务页面车源基础信息中选择车辆类型后重新采集";
  return "";
}
const isModelId = (value: string) => BigInt(value) > 0n && BigInt(value) <= 9223372036854775807n;
export function validatedModelInput(input: ModelSearchInput): ModelSearchInput {
  if (
    Object.keys(input || {}).some(
      (key) => !["announcementModel", "engineModel", "pageScope"].includes(key),
    )
  )
    throw new Error("车型查询仅接受页面固定范围及两个型号条件");
  const announcementModel = normalizeModelCode(input?.announcementModel);
  const engineModel = normalizeEngineCode(input?.engineModel);
  if (
    ![announcementModel, engineModel].every(
      (value) => !value || /^[A-Z0-9][A-Z0-9.-]{1,79}$/.test(value),
    )
  ) {
    throw new Error("请输入完整型号，保留型号后缀；不使用的条件可留空");
  }
  const pageScope = {
    brandId: string(input?.pageScope?.brandId),
    brandName: string(input?.pageScope?.brandName),
    seriesId: string(input?.pageScope?.seriesId),
    seriesName: string(input?.pageScope?.seriesName),
    typeLevel: string(input?.pageScope?.typeLevel),
  };
  const issue = modelScopeIssue(pageScope);
  if (issue) throw new Error(issue);
  return { announcementModel, engineModel, pageScope };
}

/** Only the registered model mapping is executable. Published specification/scope
 * mappings stay immutable, but no longer authorize writes owned by the business page.
 * Scope dependencies are checked against the live page before the writer runs.
 */
export function modelFillPlan(
  candidate: VehicleModelCandidate,
  mappings: readonly import("./contracts/collection").ActionMapping[] = [],
  labels: Record<string, string> = {},
  selection?: ModelPageSelection,
): { values: ModelFillValue[]; missing: string[] } {
  const model = mappings.filter((mapping) => mapping.target_field === "vehicle.model");
  if (
    model.length !== 1 ||
    model[0]!.source_kind !== "MODEL_CANDIDATE" ||
    model[0]!.source_property !== "modelName" ||
    model[0]!.source_option_property !== "modelId" ||
    model[0]!.converter_id !== "text"
  ) {
    return { values: [], missing: ["当前页面未配置唯一有效的车型回填映射"] };
  }
  const value =
    selection?.model.id === candidate.modelId && selection.model.label
      ? selection.model.label
      : candidate.modelName;
  if (!string(value)) return { values: [], missing: ["车型名称"] };
  return {
    values: [
      {
        field: "vehicle.model",
        label: labels["vehicle.model"] || "车型",
        value,
        optionValue: candidate.modelId,
      },
    ],
    missing: [],
  };
}

/** 同名记录分别保留；写回器必须按 optionValue 选择并核验真实 ID。 */
export function verifyModelOption(candidate: VehicleModelCandidate) {
  if (!/^[0-9]{1,19}$/.test(candidate.modelId) || !isModelId(candidate.modelId))
    throw new Error("车型缺少有效车型 ID，无法回填");
}

/** 消息和 Chrome storage 的对象属性顺序不属于查询/配置身份。 */
export function sameModelData(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) || Array.isArray(right))
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) => sameModelData(item, right[index]))
    );
  const a = left as Record<string, unknown>,
    b = right as Record<string, unknown>;
  const keys = Object.keys(a);
  return (
    keys.length === Object.keys(b).length &&
    keys.every((key) => Object.hasOwn(b, key) && sameModelData(a[key], b[key]))
  );
}

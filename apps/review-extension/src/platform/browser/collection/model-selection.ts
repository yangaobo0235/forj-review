import type {
  ModelPageScope,
  ModelPageSelection,
  ModelScopeResult,
  ModelSelection,
} from "../../../shared/contracts/vehicle-model";
import { modelPageScope, modelScopeIssue, sameModelData } from "../../../shared/vehicle-model";
import type { DomElement } from "../dom";
import { logicalFormControl } from "../form-controls";
import { inspectExactOption, type InspectExactOption } from "../actions/exact-select";

/** Only actual option values are identities; labels and text inputs never supply IDs. */
export async function readModelContext(
  fields: {
    pageFields: Record<string, string>;
    fieldTargets: { field: string; element: DomElement }[];
  },
  inspect: InspectExactOption = inspectExactOption,
) {
  const read = async (field: string, allowed: string[]) => {
    const targets = fields.fieldTargets.filter((item) => item.field === field);
    if (targets.length !== 1) throw new Error("品牌、车系或车型控件不唯一，请重新读取页面");
    const control = logicalFormControl(targets[0]!.element);
    if (!control || !control.isConnected) throw new Error("车型选择控件已变化，请重新读取页面");
    let id: string, label: string;
    if (control.tagName === "SELECT") {
      id = String(control.value ?? "");
      const options = Array.from(control.options || []).filter(
        (option) => String(option.value) === id,
      );
      if (id && options.length !== 1) throw new Error("选中 ID 的选项尚未加载或不唯一");
      label = id ? String(options[0]!.textContent ?? "").trim() : "";
    } else {
      const selected = await inspect(control);
      if (!allowed.includes(selected.valueField || ""))
        throw new Error("无法核验品牌、业务车系或车型 ID 字段语义");
      id = selected.selectedValue;
      label = selected.selectedLabel || "";
    }
    if (id && !/^[0-9]{1,19}$/.test(id))
      throw new Error("选中值不是有效目录 ID，请在业务页面重新选择");
    if ((id && !label) || !control.isConnected)
      throw new Error("选中身份无法核验：控件缺少选中标签，请重新读取页面");
    const normalize = (value: string) => value.normalize("NFKC").replace(/\s+/g, "");
    if (normalize(label) !== normalize(fields.pageFields[field] || ""))
      throw new Error("采集期间选中名称或 ID 已变化，请重新读取页面");
    return { id, label };
  };
  const errors: string[] = [];
  const capture = async (
    field: string,
    label: string,
    allowed: string[],
  ): Promise<ModelSelection> => {
    try {
      return await read(field, allowed);
    } catch (error) {
      errors.push(`${label}：${error instanceof Error ? error.message : "读取选项失败"}`);
      return { id: "", label: "" };
    }
  };
  const selection = {
    brand: await capture("spec.brand", "品牌", ["id", "brandId"]),
    series: await capture("spec.series", "车系", ["seriesId"]),
    model: await capture("vehicle.model", "车型", ["modelId"]),
    vehicleType: fields.pageFields["vehicle.type"] || "",
  };
  return {
    pageSelection: errors.length ? undefined : selection,
    pageModelScope: modelPageScope(selection),
    pageSelectionError: errors.join("；"),
  };
}

/** Writes require the complete snapshot; search requires the captured brand, series and basic type. */
export async function readModelSelection(
  fields: Parameters<typeof readModelContext>[0],
  inspect: InspectExactOption = inspectExactOption,
): Promise<ModelPageSelection> {
  const result = await readModelContext(fields, inspect);
  if (!result.pageSelection) throw new Error(result.pageSelectionError);
  return result.pageSelection;
}

/** Search reads the live page twice; the panel cannot replace the captured scope. */
export function createModelScopeReader<Request>(
  context: {
    isCurrent: (request: Request) => boolean;
    snapshot: () => ModelPageScope | undefined;
    collect: () => Parameters<typeof readModelContext>[0];
  },
  inspect: InspectExactOption = inspectExactOption,
) {
  return async (request: Request): Promise<ModelScopeResult> => {
    const changed = (): ModelScopeResult => ({
      ok: false,
      code: "PAGE_IDENTITY_CHANGED",
      message: "页面已变化，请重新采集后搜索车型",
    });
    if (!context.isCurrent(request)) return changed();
    const before = await readModelContext(context.collect(), inspect);
    const after = await readModelContext(context.collect(), inspect);
    if (!context.isCurrent(request)) return changed();
    const issue = modelScopeIssue(after.pageModelScope);
    if (
      issue ||
      !sameModelData(before.pageModelScope, after.pageModelScope) ||
      !sameModelData(after.pageModelScope, context.snapshot())
    ) {
      return {
        ok: false,
        code: "MODEL_PAGE_SCOPE_CHANGED",
        message: issue || "页面品牌、车系或车辆类型已变化，请重新采集后搜索车型",
      };
    }
    return { ok: true, pageScope: after.pageModelScope };
  };
}

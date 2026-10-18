import type { DomElement, DomRoot } from "../dom.ts";
import type { ModelFillResult, ModelSearchInput, SpecFillValue, VehicleModelCandidate } from "../../../shared/contracts/vehicle-model.ts";
import { modelFillPlan, modelPageScope, sameModelData, validatedModelInput, verifyModelOption } from "../../../shared/vehicle-model.ts";
import { searchVehicleModels } from "../../extension/vehicle-model-client.ts";
import { ReviewPageFieldWriter } from "./page-field-writer.ts";
import { inspectExactOption } from "./exact-select.ts";

export interface ModelFillRequest {
  expectedPageUrl?: string;
  searchId?: unknown;
  modelId?: unknown;
  [key: string]: unknown;
}
interface CollectedField {
  element: DomElement;
  collectionId: string;
  collectedValue: string;
  controlSnapshot?: {control: DomElement; value: string} | null;
}
interface ModelFillContext {
  root: DomRoot;
  isCurrent: (request: ModelFillRequest) => boolean;
  collectionId: () => string;
  fields: Map<string, CollectedField>;
  collect: (fields: readonly string[]) => {pageFields: Record<string, string>; fieldTargets: {field: string; element: DomElement}[]};
  writableFields: () => ReadonlySet<string>;
  controlKinds: () => ReadonlySet<string> | null | undefined;
  resolveSearch?: (request: ModelFillRequest) => Promise<{ok: boolean; code?: string; message: string; candidate?: VehicleModelCandidate; input?: ModelSearchInput}>;
}
class ModelFillError extends Error {
  readonly code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}
const scopeFields = ["spec.brand", "spec.series", "vehicle.type"];

/** 车型回填唯一协调器：可信候选 → 范围/配置复核 → 控件预检 → 顺序写入 → 回读快照。 */
export function createVehicleModelFiller(context: ModelFillContext) {
  let busy = false, generation = 0;
  const resolveSearch = context.resolveSearch || (request => chrome.runtime.sendMessage({...request, type: "RESOLVE_REVIEW_MODEL_SEARCH"}));
  const invalidate = () => { generation++; };
  const resolveField = (field: string) => context.collect([field]).fieldTargets.find(item => item.field === field)?.element;

  function preflight(values: SpecFillValue[]) {
    for (const item of values) {
      if (!context.writableFields().has(item.field)) throw new ModelFillError("MODEL_FIELD_UNAVAILABLE", `${item.label}不在当前页面允许回填范围内`);
      const entry = context.fields.get(item.field);
      const current = entry && ReviewPageFieldWriter.captureValue(entry.element);
      if (!entry || entry.collectionId !== context.collectionId() || !current
        || (entry.controlSnapshot && current.control !== entry.controlSnapshot.control)) {
        throw new ModelFillError("MODEL_FIELD_CHANGED", `${item.label}控件已变化或未采集，请重新采集`);
      }
      if (current.value !== (entry.controlSnapshot?.value ?? entry.collectedValue)) {
        throw new ModelFillError("MODEL_FIELD_CHANGED", `${item.label}已被修改，请重新采集后回填`);
      }
    }
  }

  function refreshSnapshots(values: SpecFillValue[], complete: boolean) {
    const actions: NonNullable<ModelFillResult["actions"]> = [];
    for (const item of values) {
      const element = resolveField(item.field);
      if (!element) continue;
      const snapshot = ReviewPageFieldWriter.captureValue(element);
      context.fields.set(item.field, {element, collectionId: context.collectionId(),
        collectedValue: snapshot?.value ?? "", controlSnapshot: snapshot});
      if (snapshot) actions.push({field: item.field, label: item.label,
        value: snapshot.value, status: complete ? "FILLED" : "UNCONFIRMED"});
    }
    return actions;
  }

  async function fill(request: ModelFillRequest): Promise<ModelFillResult> {
    if (!context.isCurrent(request)) return {ok: false, code: "PAGE_IDENTITY_CHANGED", message: "页面已变化，请重新采集", actions: []};
    if (busy) return {ok: false, code: "WRITE_IN_PROGRESS", message: "正在回填规格参数，请等待本次回填结束", actions: []};
    busy = true;
    const version = ++generation;
    const guard = () => version === generation && context.isCurrent(request);
    const checkIdentity = () => {
      if (!guard()) throw new ModelFillError("PAGE_IDENTITY_CHANGED", "页面已变化，请重新采集");
    };
    try {
      const session = await resolveSearch(request);
      checkIdentity();
      if (!session?.ok) return {...session, ok: false, message: session?.message || "车型查询已失效，请重新搜索并选择", actions: []};
      const candidate = session.candidate;
      if (!candidate || candidate.modelId !== request.modelId) throw new ModelFillError("MODEL_SEARCH_CHANGED", "未读到所选车型，请重新搜索并选择");
      const input = validatedModelInput(session.input!);
      const checkScope = () => {
        checkIdentity();
        if (!sameModelData(modelPageScope(context.collect(scopeFields).pageFields), input.pageScope)) {
          throw new ModelFillError("MODEL_PAGE_SCOPE_CHANGED", "页面品牌、车系或车辆类型已变化，请重新读取页面并搜索车型");
        }
      };
      checkScope();
      const latest = (await searchVehicleModels(input, guard, request.expectedPageUrl!)).find(item => item.modelId === candidate.modelId);
      checkScope();
      if (!sameModelData(latest, candidate)) throw new ModelFillError("MODEL_CONFIG_CHANGED", "离线目录中的车型配置已更新，请重新搜索并核对回填预览");
      verifyModelOption(candidate);
      const plan = modelFillPlan(candidate, Object.fromEntries([...context.fields].map(([field, entry]) => [field, entry.collectedValue])));
      if (plan.missing.length) throw new ModelFillError("MODEL_CONFIG_INCOMPLETE", `车型配置缺少：${plan.missing.join("、")}，请先人工补齐`);
      preflight(plan.values);
      checkScope();
      let result: ModelFillResult;
      try {
        result = await ReviewPageFieldWriter.executeSequence(context.root, plan.values, resolveField, guard,
          {fields: context.writableFields(), controlKinds: context.controlKinds(), inspectOption: inspectExactOption,
            label: "规格参数", focusField: "vehicle.model", settleAfter: {"vehicle.model": 15}});
      } catch (error) {
        result = {ok: false, code: "MODEL_WRITE_INTERRUPTED", message: `${error && typeof error === "object" && "message" in error
          ? String(error.message) : "规格参数写入中断"}；请核对已写入字段`};
      }
      checkIdentity();
      // 只有本记录仍有效才同步实际读回值。部分成功绝不能标成整块完成。
      return {...result, actions: refreshSnapshots(plan.values, result.ok), warnings: plan.warnings};
    } catch (error) {
      return {ok: false, code: !guard() ? "PAGE_IDENTITY_CHANGED" : error instanceof ModelFillError ? error.code : "MODEL_FILL_FAILED",
        message: error && typeof error === "object" && "message" in error ? String(error.message) : "车型查询或回填失败，请重试", actions: []};
    } finally { busy = false; }
  }
  return {fill, invalidate};
}

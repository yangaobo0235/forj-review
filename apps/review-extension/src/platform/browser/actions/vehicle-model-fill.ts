import type { DomElement, DomRoot } from "../dom.ts";
import type {
  ModelPageSelection,
  ModelFillResult,
  ModelSearchInput,
  ModelFillValue,
  VehicleModelCandidate,
} from "../../../shared/contracts/vehicle-model.ts";
import {
  modelFillPlan,
  modelPageScope,
  sameModelData,
  validatedModelInput,
  verifyModelOption,
} from "../../../shared/vehicle-model.ts";
import { searchVehicleModels } from "../../extension/vehicle-model-client.ts";
import { ReviewPageFieldWriter } from "./page-field-writer.ts";
import { readModelSelection } from "../collection/model-selection";
import { inspectExactOption } from "./exact-select.ts";
import type { ActionMapping } from "../../../shared/contracts/collection";

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
  controlSnapshot?: { control: DomElement; value: string } | null;
}
interface ModelFillContext {
  root: DomRoot;
  isCurrent: (request: ModelFillRequest) => boolean;
  collectionId: () => string;
  selectionSnapshot: () => ModelPageSelection | undefined;
  fields: Map<string, CollectedField>;
  collect: (fields: readonly string[]) => {
    pageFields: Record<string, string>;
    fieldTargets: { field: string; element: DomElement }[];
  };
  writableFields: () => ReadonlySet<string>;
  controlKinds: () => ReadonlySet<string> | null | undefined;
  mappings?: () => readonly ActionMapping[];
  fieldLabels?: () => Record<string, string>;
  resolveSearch?: (request: ModelFillRequest) => Promise<{
    ok: boolean;
    code?: string;
    message: string;
    candidate?: VehicleModelCandidate;
    input?: ModelSearchInput;
  }>;
}
class ModelFillError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}
const scopeFields = ["spec.brand", "spec.series", "vehicle.model", "vehicle.type"];

/** 车型回填唯一协调器：可信候选 → 范围复核 → 控件预检 → 选择车型 → 回读 ID。 */
export function createVehicleModelFiller(context: ModelFillContext) {
  let busy = false,
    generation = 0;
  let verifiedSelection: { collectionId: string; selection: ModelPageSelection } | undefined;
  const resolveSearch =
    context.resolveSearch ||
    ((request) => chrome.runtime.sendMessage({ ...request, type: "RESOLVE_REVIEW_MODEL_SEARCH" }));
  const invalidate = () => {
    generation++;
  };
  const resolveField = (field: string) =>
    context.collect([field]).fieldTargets.find((item) => item.field === field)?.element;

  function preflight(values: ModelFillValue[]) {
    for (const item of values) {
      if (!context.writableFields().has(item.field))
        throw new ModelFillError(
          "MODEL_FIELD_UNAVAILABLE",
          `${item.label}不在当前页面允许回填范围内`,
        );
      const entry = context.fields.get(item.field);
      const current = entry && ReviewPageFieldWriter.captureValue(entry.element);
      if (
        !entry ||
        entry.collectionId !== context.collectionId() ||
        !current ||
        (entry.controlSnapshot && current.control !== entry.controlSnapshot.control)
      ) {
        throw new ModelFillError(
          "MODEL_FIELD_CHANGED",
          `${item.label}控件已变化或未采集，请重新采集`,
        );
      }
      if (current.value !== (entry.controlSnapshot?.value ?? entry.collectedValue)) {
        throw new ModelFillError("MODEL_FIELD_CHANGED", `${item.label}已被修改，请重新采集后回填`);
      }
    }
  }

  function refreshSnapshots(values: ModelFillValue[], complete: boolean) {
    const actions: NonNullable<ModelFillResult["actions"]> = [];
    for (const item of values) {
      const element = resolveField(item.field);
      if (!element) continue;
      const snapshot = ReviewPageFieldWriter.captureValue(element);
      context.fields.set(item.field, {
        element,
        collectionId: context.collectionId(),
        collectedValue: snapshot?.value ?? "",
        controlSnapshot: snapshot,
      });
      if (snapshot)
        actions.push({
          field: item.field,
          label: item.label,
          value: snapshot.value,
          status: complete ? "FILLED" : "UNCONFIRMED",
        });
    }
    return actions;
  }

  async function fill(request: ModelFillRequest): Promise<ModelFillResult> {
    if (!context.isCurrent(request))
      return {
        ok: false,
        code: "PAGE_IDENTITY_CHANGED",
        message: "页面已变化，请重新采集",
        actions: [],
      };
    if (busy)
      return {
        ok: false,
        code: "WRITE_IN_PROGRESS",
        message: "正在回填车型，请等待本次回填结束",
        actions: [],
      };
    busy = true;
    const version = ++generation;
    const guard = () => version === generation && context.isCurrent(request);
    const checkIdentity = () => {
      if (!guard()) throw new ModelFillError("PAGE_IDENTITY_CHANGED", "页面已变化，请重新采集");
    };
    try {
      const session = await resolveSearch(request);
      checkIdentity();
      if (!session?.ok)
        return {
          ...session,
          ok: false,
          message: session?.message || "车型查询已失效，请重新搜索并选择",
          actions: [],
        };
      const candidate = session.candidate;
      if (!candidate || candidate.modelId !== request.modelId)
        throw new ModelFillError("MODEL_SEARCH_CHANGED", "未读到所选车型，请重新搜索并选择");
      const input = validatedModelInput(session.input!);
      const original =
        verifiedSelection?.collectionId === context.collectionId()
          ? verifiedSelection.selection
          : context.selectionSnapshot();
      if (!original || !sameModelData(input.pageScope, modelPageScope(original))) {
        throw new ModelFillError(
          "MODEL_PAGE_SCOPE_CHANGED",
          "所选车型不属于本次页面品牌、车辆类型或选定车系，请重新搜索",
        );
      }
      const checkScope = async () => {
        checkIdentity();
        if (
          !original ||
          !sameModelData(await readModelSelection(context.collect(scopeFields)), original)
        ) {
          throw new ModelFillError(
            "MODEL_PAGE_SCOPE_CHANGED",
            "页面品牌、车系、车型或车辆类型已变化，或未取得选项快照，请重新读取页面并搜索车型",
          );
        }
        checkIdentity();
      };
      await checkScope();
      const latest = (await searchVehicleModels(input, guard, request.expectedPageUrl!)).find(
        (item) => item.modelId === candidate.modelId,
      );
      await checkScope();
      if (!sameModelData(latest, candidate))
        throw new ModelFillError(
          "MODEL_CANDIDATE_CHANGED",
          "业务接口中的候选车型已更新，请重新搜索并核对回填预览",
        );
      verifyModelOption(candidate);
      const mappings = context.mappings?.() ?? [];
      const plan = modelFillPlan(candidate, mappings, context.fieldLabels?.() ?? {}, original);
      if (plan.missing.length)
        throw new ModelFillError(
          "MODEL_MAPPING_INVALID",
          `无法回填车型：${plan.missing.join("、")}`,
        );
      // Brand and series stay fixed; model selection triggers the native detail linkage.
      const writeValues = plan.values;
      if (writeValues.length !== 1 || writeValues[0]?.field !== "vehicle.model") {
        throw new ModelFillError("MODEL_MAPPING_INVALID", "车型回填只能写入车型字段");
      }
      await checkScope();
      preflight(writeValues);
      let result: ModelFillResult;
      try {
        result = await ReviewPageFieldWriter.executeSequence(
          context.root,
          writeValues,
          resolveField,
          guard,
          {
            useLiveOptionLabels: true,
            fields: context.writableFields(),
            controlKinds: context.controlKinds(),
            inspectOption: inspectExactOption,
            optionValueFields: { "vehicle.model": ["modelId"] },
            label: "车型",
            focusField: "vehicle.model",
          },
        );
        if (result.ok)
          result = {
            ...result,
            message: "车型已回填并核验；其余规格参数由原业务系统自动带入，请核对页面",
          };
      } catch (error) {
        result = {
          ok: false,
          code: "MODEL_WRITE_INTERRUPTED",
          message: `${
            error && typeof error === "object" && "message" in error
              ? String(error.message)
              : "车型写入中断"
          }；请核对已写入字段`,
        };
      }
      checkIdentity();
      // A confirmed model selection can become the next baseline even if final readback fails.
      // Unrelated page changes still invalidate the collection.
      const confirmed = (field: string) =>
        result.actions?.some(
          (action) => action.field === field && ["FILLED", "UNCHANGED"].includes(action.status),
        );
      if (result.ok || confirmed("vehicle.model")) {
        try {
          const selection = await readModelSelection(context.collect(scopeFields));
          checkIdentity();
          if (selection.vehicleType !== original.vehicleType) {
            result = {
              ok: false,
              code: "MODEL_PAGE_SCOPE_CHANGED",
              message: "回填期间页面车辆类型已变化，请重新采集并核对已写入字段",
            };
          } else if (
            selection.brand.id !== original.brand.id ||
            selection.series.id !== original.series.id ||
            selection.model.id !== candidate.modelId
          ) {
            result = {
              ok: false,
              code: "MODEL_FIELD_CHANGED",
              message: "回填后选项 ID 已变化，请重新采集并核对已写入字段",
            };
          } else verifiedSelection = { collectionId: context.collectionId(), selection };
        } catch {
          checkIdentity();
          result = {
            ok: false,
            code: "MODEL_READBACK_FAILED",
            message: "车型写入后无法核验选项身份，请核对已写入字段并重新采集",
          };
        }
      }
      // 只有本记录仍有效才同步实际读回值。写入未确认时不能报告成功。
      return { ...result, actions: refreshSnapshots(writeValues, result.ok) };
    } catch (error) {
      return {
        ok: false,
        code: !guard()
          ? "PAGE_IDENTITY_CHANGED"
          : error instanceof ModelFillError
            ? error.code
            : "MODEL_FILL_FAILED",
        message:
          error && typeof error === "object" && "message" in error
            ? String(error.message)
            : "车型查询或回填失败，请重试",
        actions: [],
      };
    } finally {
      busy = false;
    }
  }
  return { fill, invalidate };
}

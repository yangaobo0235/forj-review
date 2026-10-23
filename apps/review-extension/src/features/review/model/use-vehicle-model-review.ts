import { computed, reactive } from "vue";
import type { PageData, ReviewResponse } from "../../../shared/contracts/review";
import type { ModelFillResult } from "../../../shared/contracts/vehicle-model";
import {
  queryVehicleModels,
  vehicleModelMessage,
} from "../../../platform/extension/vehicle-model-client";
import { modelFillPlan, verifyModelOption } from "../../../shared/vehicle-model";
import { useVehicleModelSearch } from "./use-vehicle-model-search";

interface Options {
  review: () => ReviewResponse;
  pageData: () => PageData | null | undefined;
  disabled: () => boolean;
  onFill: () => ((searchId: string, modelId: string) => Promise<ModelFillResult>) | undefined;
  onBusy: (value: boolean) => void;
  onResult: (result: ModelFillResult) => void;
}
const INVALID_SEARCH_CODES = new Set([
  "MODEL_SEARCH_CHANGED",
  "MODEL_CANDIDATE_CHANGED",
  "MODEL_PAGE_SOURCE_INVALID",
  "MODEL_PAGE_SCOPE_CHANGED",
  "MODEL_FIELD_CHANGED",
  "MODEL_READBACK_FAILED",
]);

/** 工作台协调：搜索独立维护候选；此处只负责回填预览、操作状态及结果。 */
export function useVehicleModelReview(options: Options, query = queryVehicleModels) {
  const operation = reactive({ filling: false, fillMessage: "" });
  const search = useVehicleModelSearch(
    {
      review: options.review,
      pageData: options.pageData,
      disabled: () => options.disabled() || operation.filling,
      onInvalidate: () => {
        operation.fillMessage = "";
      },
      onPageFailure: options.onResult,
    },
    query,
  );
  const plan = computed(() =>
    search.selected.value
      ? modelFillPlan(
          search.selected.value,
          options
            .pageData()
            ?.actions?.find((a) => a.handler_id === "vehicle_model_fill" && a.enabled)?.mappings ??
            [],
          options.pageData()?.fieldLabels,
          options.pageData()?.pageSelection,
        )
      : null,
  );
  const changedCount = computed(
    () =>
      plan.value?.values.filter((item) => {
        const page = options.pageData(),
          current = page?.pageSelection?.model;
        return item.optionValue
          ? current?.id !== item.optionValue
          : String(page?.pageFields[item.field] ?? "").trim() !== item.value.trim();
      }).length || 0,
  );
  const fillDisabledReason = computed(() => {
    if (operation.filling) return "正在回填并核验车型，请稍候";
    if (options.disabled()) return "当前审核不可操作，请先完成页面读取或处理页面失效提示";
    if (!options.pageData()) return "请先读取审核页面";
    if (!options.pageData()?.pageSelection) return "未取得原页面选项身份，请重新读取页面后回填";
    if (search.state.status === "SEARCHING") return "正在查询候选车型";
    if (search.state.status === "ERROR") return "车型查询失败，请重试成功后选择候选车型";
    if (!search.selected.value || !search.state.searchId) return "请先选择候选车型";
    if (plan.value?.missing.length)
      return `配置缺少：${plan.value.missing.join("、")}，无法完整回填`;
    try {
      verifyModelOption(search.selected.value);
    } catch (error) {
      return vehicleModelMessage(error);
    }
    if (!options.onFill()) return "车型回填尚未配置";
    return "";
  });
  async function fill() {
    const action = options.onFill(),
      candidate = search.selected.value;
    if (fillDisabledReason.value || !candidate || !action) return;
    const record = search.identity.value;
    operation.filling = true;
    operation.fillMessage = "正在回填并核验车型…";
    options.onBusy(true);
    try {
      const result = await action(search.state.searchId, candidate.modelId);
      if (!search.isCurrent(record)) return;
      const safe = { ...result, message: vehicleModelMessage(result.message) };
      if (INVALID_SEARCH_CODES.has(result.code || "")) search.fail(safe.message);
      operation.fillMessage = safe.message;
      options.onResult(safe);
    } catch (error) {
      if (search.isCurrent(record)) {
        operation.fillMessage = `${vehicleModelMessage(error)}；请检查原页面已写入的字段后再操作`;
        options.onResult({ ok: false, message: operation.fillMessage });
      }
    } finally {
      operation.filling = false;
      options.onBusy(false);
    }
  }
  const { state, source, pageScope, scopeReady, selected, edit, select, restoreMaterials } = search;
  return reactive({
    state,
    operation,
    source,
    pageScope,
    scopeReady,
    selected,
    plan,
    changedCount,
    fillDisabledReason,
    edit,
    select,
    search: () => search.search(),
    restoreMaterials,
    fill,
  });
}

export type VehicleModelReview = ReturnType<typeof useVehicleModelReview>;

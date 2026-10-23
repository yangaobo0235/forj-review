import { computed, onScopeDispose, reactive, watch } from "vue";
import type { PageData, ReviewResponse } from "../../../shared/contracts/review";
import type {
  ModelSearchInput,
  VehicleModelCandidate,
} from "../../../shared/contracts/vehicle-model";
import {
  modelPageScope,
  modelScopeIssue,
  normalizeEngineCode,
  normalizeModelCode,
  validatedModelInput,
} from "../../../shared/vehicle-model";
import {
  queryVehicleModels,
  vehicleModelMessage,
} from "../../../platform/extension/vehicle-model-client";

interface Options {
  review: () => ReviewResponse;
  pageData: () => PageData | null | undefined;
  disabled: () => boolean;
  onInvalidate: () => void;
  onPageFailure: (result: { ok: false; code: string; message: string }) => void;
}
type SearchStatus = "WAITING" | "READY" | "EDITED" | "SEARCHING" | "FOUND" | "EMPTY" | "ERROR";

/** Brand, series and basic vehicle type are fixed to the captured business page. */
export function useVehicleModelSearch(options: Options, query = queryVehicleModels) {
  const state = reactive({
    input: { announcementModel: "", engineModel: "" },
    manual: false,
    status: "WAITING" as SearchStatus,
    message: "等待页面品牌、车系和车辆类型",
    candidates: [] as VehicleModelCandidate[],
    searchId: "",
    selectedId: "",
  });
  const source = computed(
    () =>
      options.review().cross_checks?.find((item) => item.check_id === "VEHICLE-SOURCE-MODEL")
        ?.details?.model_search,
  );
  const materialInput = () => ({
    announcementModel: normalizeModelCode(source.value?.announcement_model),
    engineModel: normalizeEngineCode(source.value?.engine_model),
  });
  const materialIdentity = computed(() => JSON.stringify([!!source.value, materialInput()]));
  const pageScope = computed(
    () => options.pageData()?.pageModelScope ?? modelPageScope(options.pageData()?.pageSelection),
  );
  const scopeReady = computed(() => !modelScopeIssue(pageScope.value));
  const identity = computed(() => {
    const page = options.pageData();
    return JSON.stringify([
      options.review().business_type,
      page?.sourceTabId,
      page?.pageUrl,
      page?.pageInstanceId,
      page?.pageFingerprint,
      page?.collectionId,
      page?.pageSelection,
      pageScope.value,
    ]);
  });
  let generation = 0,
    live = true,
    previousIdentity = "",
    previousMaterial = "",
    attempted = false;
  const isCurrent = (record: string) => live && identity.value === record;
  const selected = computed(() =>
    state.candidates.find((item) => item.modelId === state.selectedId),
  );
  function invalidate() {
    generation++;
    state.candidates = [];
    state.selectedId = "";
    state.searchId = "";
    options.onInvalidate();
  }
  function fail(message: string) {
    invalidate();
    state.status = "ERROR";
    state.message = message;
  }
  function waitingMessage() {
    state.status = scopeReady.value && source.value ? "READY" : "WAITING";
    state.message =
      modelScopeIssue(pageScope.value) ||
      (source.value
        ? "可核对原图后填写型号，或直接浏览当前页面范围内的车型"
        : "等待图片型号识别；也可核对原图后手动搜索");
  }
  function edited() {
    invalidate();
    state.status = "EDITED";
    state.message = "搜索条件已修改，请点击搜索车型";
  }
  function edit(field: "announcementModel" | "engineModel", value: string) {
    if (options.disabled()) return;
    state.manual = true;
    state.input[field] = value;
    edited();
  }
  function select(id: string) {
    if (
      !options.disabled() &&
      state.status === "FOUND" &&
      state.candidates.some((item) => item.modelId === id)
    ) {
      state.selectedId = id;
      options.onInvalidate();
    }
  }
  async function search() {
    const page = options.pageData();
    if (!page || options.disabled() || state.status === "SEARCHING") return;
    let input: ModelSearchInput;
    try {
      input = validatedModelInput({ ...state.input, pageScope: pageScope.value });
    } catch (error) {
      fail(vehicleModelMessage(error));
      return;
    }
    const record = identity.value;
    attempted = true;
    invalidate();
    const version = generation;
    const current = () => isCurrent(record) && generation === version;
    state.status = "SEARCHING";
    state.message = "正在当前品牌、车系及车辆类型内按型号条件查询…";
    try {
      const result = await query(page, input);
      if (!current()) return;
      if (!result?.ok) {
        fail(vehicleModelMessage(result?.message));
        if (["PAGE_IDENTITY_CHANGED", "MODEL_PAGE_SCOPE_CHANGED"].includes(result?.code || ""))
          options.onPageFailure({ ok: false, code: result.code!, message: state.message });
        return;
      }
      if (!Array.isArray(result.candidates) || (result.candidates.length && !result.searchId))
        throw new Error("车型查询响应不完整，请重试");
      state.candidates = result.candidates;
      state.searchId = result.searchId || "";
      state.status = state.candidates.length ? "FOUND" : "EMPTY";
      state.message = state.candidates.length
        ? `找到 ${state.candidates.length} 条候选车型记录，请核对并选择`
        : "当前品牌、车系及车辆类型内没有匹配车型；请核对或清空型号条件后重新搜索";
    } catch (error) {
      if (current()) fail(vehicleModelMessage(error));
    }
  }
  function autoSearch() {
    if (
      options.review().business_type !== "vehicle_source" ||
      options.disabled() ||
      state.manual ||
      !scopeReady.value ||
      !source.value ||
      attempted
    )
      return;
    if (state.input.announcementModel || state.input.engineModel) void search();
  }
  function restoreMaterials() {
    if (options.disabled()) return;
    state.manual = false;
    attempted = false;
    Object.assign(state.input, materialInput());
    invalidate();
    waitingMessage();
    autoSearch();
  }
  watch(
    [identity, materialIdentity, options.disabled],
    ([nextIdentity, nextMaterial]) => {
      const pageChanged = previousIdentity !== nextIdentity;
      if (pageChanged) state.manual = false;
      if (pageChanged || (!state.manual && previousMaterial !== nextMaterial)) {
        invalidate();
        attempted = false;
        Object.assign(state.input, materialInput());
        waitingMessage();
      }
      previousIdentity = nextIdentity;
      previousMaterial = nextMaterial;
      autoSearch();
    },
    { immediate: true, flush: "sync" },
  );
  onScopeDispose(() => {
    live = false;
    generation++;
  });
  return {
    state,
    source,
    pageScope,
    scopeReady,
    identity,
    isCurrent,
    selected,
    fail,
    edit,
    select,
    search,
    restoreMaterials,
  };
}

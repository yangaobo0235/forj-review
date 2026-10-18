import { computed, onScopeDispose, reactive, watch } from "vue";
import type { PageData, ReviewResponse } from "../../../shared/contracts/review";
import type { ModelSearchInput, VehicleModelCandidate } from "../../../shared/contracts/vehicle-model";
import { modelPageScope, modelScopeIssue, normalizeEngineCode, normalizeModelCode, validatedModelInput } from "../../../shared/vehicle-model";
import { queryVehicleModels, vehicleModelMessage } from "../../../platform/extension/vehicle-model-client";

interface Options {
  review: () => ReviewResponse;
  pageData: () => PageData | null | undefined;
  disabled: () => boolean;
  onInvalidate: () => void;
  onPageFailure: (result: {ok: false; code: string; message: string}) => void;
}
type SearchStatus = "WAITING" | "READY" | "EDITED" | "SEARCHING" | "FOUND" | "EMPTY" | "ERROR";
const inputKey = (input: Pick<ModelSearchInput, "announcementModel" | "engineModel">) =>
  JSON.stringify([normalizeModelCode(input.announcementModel), normalizeEngineCode(input.engineModel)]);

/** 材料条件、人工条件、候选与分页属于同一查询；编辑或换记录立即使旧请求失效。 */
export function useVehicleModelSearch(options: Options, query = queryVehicleModels) {
  const state = reactive({
    input: {announcementModel: "", engineModel: ""}, manual: false,
    status: "WAITING" as SearchStatus, message: "等待材料中的整车型号和发动机型号",
    candidates: [] as VehicleModelCandidate[], searchId: "", selectedId: "", page: 1,
  });
  const source = computed(() => options.review().cross_checks?.find(item => item.check_id === "VEHICLE-SOURCE-MODEL")?.details?.model_search);
  const materialInput = () => ({announcementModel: source.value?.announcement_model || "", engineModel: source.value?.engine_model || ""});
  const pageScope = computed(() => modelPageScope(options.pageData()?.pageFields));
  const scopeIssue = computed(() => modelScopeIssue(pageScope.value));
  const scopeReady = computed(() => !scopeIssue.value);
  const identity = computed(() => {
    const page = options.pageData();
    return JSON.stringify([options.review().business_type, page?.sourceTabId, page?.pageUrl,
      page?.pageInstanceId, page?.pageFingerprint, page?.collectionId, pageScope.value]);
  });
  let generation = 0, live = true, previousIdentity = "", materialKey = "", attemptedKey = "";
  const isCurrent = (record: string) => live && identity.value === record;
  const selected = computed(() => state.candidates.find(item => item.modelId === state.selectedId));
  const pageSize = 5;
  const pageCount = computed(() => Math.ceil(state.candidates.length / pageSize));
  const pageCandidates = computed(() => state.candidates.slice((state.page - 1) * pageSize, state.page * pageSize));
  const selectedPage = computed(() => selected.value ? Math.floor(state.candidates.indexOf(selected.value) / pageSize) + 1 : 0);
  const range = computed(() => state.candidates.length
    ? `${(state.page - 1) * pageSize + 1}–${Math.min(state.page * pageSize, state.candidates.length)}` : "0–0");
  const pageButtons = computed(() => {
    const pages = Array.from(new Set([1, state.page - 1, state.page, state.page + 1, pageCount.value]))
      .filter(page => page > 0 && page <= pageCount.value).sort((a, b) => a - b);
    return pages.flatMap((page, index) => index && page - pages[index - 1]! > 1 ? ["…", page] : [page]);
  });

  function invalidate() {
    generation++;
    state.candidates = []; state.selectedId = ""; state.searchId = ""; state.page = 1;
    options.onInvalidate();
  }
  function fail(message: string) {
    invalidate(); state.status = "ERROR"; state.message = message;
  }
  function waitingMessage() {
    if (!scopeReady.value) {
      state.status = "WAITING"; state.message = scopeIssue.value; return;
    }
    const missing = [!state.input.announcementModel.trim() && "整车型号", !state.input.engineModel.trim() && "发动机型号"].filter(Boolean);
    state.status = missing.length ? "WAITING" : "READY";
    state.message = missing.length ? `缺少${missing.join("和")}，请核对材料或手动输入` : "材料型号已就绪，等待查询";
  }
  function edit(field: "announcementModel" | "engineModel", value: string) {
    if (options.disabled()) return;
    state.manual = true; state.input[field] = value;
    invalidate(); state.status = "EDITED"; state.message = "搜索条件已修改，请点击搜索车型";
  }
  function setPage(page: number) {
    if (!options.disabled() && Number.isInteger(page) && page >= 1 && page <= pageCount.value) state.page = page;
  }
  function select(id: string) {
    if (!options.disabled() && state.status === "FOUND" && state.candidates.some(item => item.modelId === id)) {
      state.selectedId = id; options.onInvalidate();
    }
  }
  async function search() {
    const page = options.pageData();
    if (!page || options.disabled() || state.status === "SEARCHING") return;
    let input: ModelSearchInput;
    try { input = validatedModelInput({...state.input, pageScope: pageScope.value}); }
    catch (error) { fail(vehicleModelMessage(error)); return; }
    const record = identity.value, key = inputKey(input);
    attemptedKey = key;
    invalidate();
    const version = generation;
    const current = () => isCurrent(record) && generation === version && inputKey(state.input) === key;
    state.status = "SEARCHING"; state.message = "正在当前品牌、车系和车辆类型范围内查询…";
    try {
      const result = await query(page, input);
      if (!current()) return;
      if (!result?.ok) {
        fail(vehicleModelMessage(result?.message));
        if (result?.code === "PAGE_IDENTITY_CHANGED") options.onPageFailure({ok: false, code: result.code, message: state.message});
        return;
      }
      if (!Array.isArray(result.candidates) || (result.candidates.length && !result.searchId)) throw new Error("车型查询响应不完整，请重试");
      state.candidates = result.candidates; state.searchId = result.searchId || "";
      state.status = result.candidates.length ? "FOUND" : "EMPTY";
      state.message = result.candidates.length ? `找到 ${result.candidates.length} 条候选车型记录，请核对并选择`
        : "当前品牌、车系和车辆类型范围内未找到同时匹配两个型号的车型，请核对页面选择和材料型号";
    } catch (error) {
      if (current()) fail(vehicleModelMessage(error));
    }
  }
  function useMaterials() {
    if (options.disabled()) return;
    state.manual = false; materialKey = inputKey(materialInput()); Object.assign(state.input, materialInput());
    invalidate(); waitingMessage();
    if (state.status === "READY") void search();
  }
  watch([identity, () => inputKey(materialInput()), options.disabled], ([nextIdentity, nextMaterial]) => {
    if (previousIdentity !== nextIdentity) {
      invalidate(); previousIdentity = nextIdentity; materialKey = ""; attemptedKey = ""; state.manual = false;
    }
    if (materialKey !== nextMaterial) {
      materialKey = nextMaterial;
      if (!state.manual) { Object.assign(state.input, materialInput()); invalidate(); waitingMessage(); }
    }
    if (options.review().business_type !== "vehicle_source" || options.disabled() || state.manual || !scopeReady.value) return;
    if (state.input.announcementModel.trim() && state.input.engineModel.trim() && attemptedKey !== inputKey(state.input)) void search();
  }, {immediate: true, flush: "sync"});
  onScopeDispose(() => { live = false; generation++; });
  return {state, source, pageScope, scopeReady, identity, isCurrent, selected, fail,
    pageCount, pageCandidates, selectedPage, range, pageButtons, setPage, edit, select, search, useMaterials};
}

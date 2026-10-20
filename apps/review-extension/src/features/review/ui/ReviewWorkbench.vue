<script setup lang="ts">
import { computed, ref, shallowReactive, watch } from "vue";
import ReviewStepDetail from "./ReviewStepDetail.vue";
import VehicleModelPicker from "./VehicleModelPicker.vue";
import { useVehicleModelReview } from "../model/use-vehicle-model-review";
import { workbenchViews, fieldFromStep, isExternalTask, statusText, stepTitle, type View, type Decision } from "../model/workbench";
import { isBlockingPageFillFailure, type PageFillResult } from "../../../shared/contracts/page-fill";
import { isFieldFirstTaskVisible } from "../model/review-tasks";
import type { ReviewTask } from "../../../shared/contracts/review";
import type { WorkbenchProps } from "./workbench-props";
import "./EvidenceReviewWorkbench.css";

const props = withDefaults(defineProps<WorkbenchProps>(), {
  readOnly: false,
  onFocusImage: async () => ({ok: false, error: "原图定位未配置"}),
  onApplyPageFieldValue: async () => ({ok: false, message: "页面写回未配置"}),
  onApplyPageFieldGroupValue: async () => ({ok: false, message: "页面组合字段写回未配置"}),
});
const state = shallowReactive({view: "PENDING" as View, selectedId: null as string | null,
  decisions: {} as Record<string, Decision>, message: "", filledCount: 0, busy: false, blockingIssue: ""});
const model = useVehicleModelReview({
  review: () => props.review, pageData: () => props.pageData,
  disabled: () => props.readOnly || state.busy || Boolean(state.blockingIssue),
  onFill: () => props.onApplyVehicleModel, onBusy: value => { state.busy = value; },
  onResult: result => {
    if (result.ok) { state.filledCount++; state.message = result.message; }
    else handleWriteFailure(result);
  },
});
// 展示策略由后端投影；隐藏任务不进入任何视图、计数或默认选择。
const displaySteps = computed(() => [...(props.review.review_tasks ?? [])]
  .sort((a, b) => a.sequence - b.sequence).filter(isFieldFirstTaskVisible));
const pending = computed(() => displaySteps.value.filter(step => step.requires_reviewer_action && !state.decisions[step.step_id]));
const visible = computed(() => state.view === "PENDING" ? pending.value : state.view === "EXTERNAL"
  ? displaySteps.value.filter(isExternalTask) : displaySteps.value.filter(step => step.category === "FIELD"));
const current = computed(() => visible.value.find(step => step.step_id === state.selectedId) ?? visible.value[0] ?? null);
const modelStep = computed(() => props.review.business_type === "vehicle_source" && current.value && fieldFromStep(current.value) === "vehicle.model");
const indexElement = ref<HTMLElement | null>(null);
// Only scroll the field list when its selection changes; preserve the page reading position.
watch([() => state.view, () => current.value?.step_id], () => {
  const index = indexElement.value;
  const selected = index?.querySelector<HTMLElement>('[aria-pressed="true"]');
  if (!index || !selected) return;
  const container = index.getBoundingClientRect(), item = selected.getBoundingClientRect();
  if (item.top < container.top) index.scrollTop -= container.top - item.top;
  else if (item.bottom > container.bottom) index.scrollTop += item.bottom - container.bottom;
}, { flush: "post" });
const allDone = computed(() => pending.value.length === 0);
const automaticCount = computed(() => displaySteps.value.filter(step => !step.requires_reviewer_action).length);
const tabs = computed(() => workbenchViews({pending: pending.value.length,
  fieldCount: displaySteps.value.filter(step => step.category === "FIELD").length,
  externalCount: displaySteps.value.filter(isExternalTask).length}));
function selectView(view: View) { state.view = view; state.selectedId = null; state.message = ""; }
function selectStep(id: string) { state.selectedId = id; state.message = ""; }
function choose(step: ReviewTask, decision: Decision) {
  state.decisions = {...state.decisions, [step.step_id]: decision};
  state.selectedId = null; state.message = "已记录本项人工处置";
}
function handleWriteFailure(reason: unknown) {
  const failure = typeof reason === "object" && reason && "message" in reason
    ? reason as Pick<PageFillResult, "message" | "code"> : {message: typeof reason === "string" ? reason : "页面回填失败"};
  state.message = failure.message;
  if (isBlockingPageFillFailure(failure)) state.blockingIssue = failure.message;
}
async function focus(imageId: string) {
  state.message = "正在定位原图…";
  const result = await props.onFocusImage(imageId);
  state.message = result?.ok ? "已定位到原图，页面已滚动并高亮该图；如未看到请检查原审核页面"
    : result?.error || "原图定位失败，请重新采集";
}
async function fill(step: ReviewTask, value: string) {
  const field = fieldFromStep(step), pageData = props.pageData;
  if (!field || !pageData || props.readOnly || state.busy || state.blockingIssue) return;
  const fields = step.page_target_fields?.length ? step.page_target_fields : [field];
  const expectedValues = Object.fromEntries(fields.map(target => [target, pageData.pageFields[target] ?? null]));
  state.busy = true;
  state.message = fields.length > 1 ? "正在联合回填并回读页面字段..." : "正在回填并回读页面字段...";
  try {
    const result = fields.length > 1 ? await props.onApplyPageFieldGroupValue(fields, value, expectedValues)
      : await props.onApplyPageFieldValue(field, value, expectedValues[field]);
    if (result.ok) {
      state.filledCount++;
      state.message = `${stepTitle(step)}已${fields.length > 1 ? "同时回填两个字段并" : ""}回读，已定位到页面字段；核对后请点击“标记人工复核”`;
    } else handleWriteFailure(result);
  } catch (error) { handleWriteFailure(error); }
  finally { state.busy = false; }
}
</script>

<template>
  <section class="review-workbench" aria-label="字段证据审核工作台">
    <header class="workbench-summary"><div><strong>字段证据审核</strong><small>自动通过 {{ automaticCount }} · 已人工处理 {{ Object.keys(state.decisions).length }}</small></div><b v-if="state.blockingIssue || allDone">{{ state.blockingIssue ? "页面已失效" : "展示项目已处理" }}</b></header>
    <div v-if="state.blockingIssue" class="workbench-blocked" role="alert"><strong>页面操作已停止</strong><span>{{ state.blockingIssue }}</span><button v-if="onRerun" type="button" @click="onRerun">重新采集并复核</button></div>
    <nav class="workbench-tabs" aria-label="审核视图"><button v-for="[key, label] in tabs" :key="key" type="button" :aria-pressed="state.view === key" :class="{active: state.view === key}" :disabled="state.busy" @click="selectView(key)">{{ label }}</button></nav>
    <div class="workbench-content">
      <nav ref="indexElement" class="workbench-index" aria-label="核验项目"><button v-for="item in visible" :key="item.step_id" type="button" :title="stepTitle(item)" :aria-pressed="current?.step_id === item.step_id" :class="{selected: current?.step_id === item.step_id}" :disabled="state.busy" @click="selectStep(item.step_id)"><span>{{ stepTitle(item) }}</span><b :class="`status-${item.result_status.toLowerCase()}`">{{ state.decisions[item.step_id] ? "已处理" : item.details?.not_applicable && item.result_status === 'MATCH' ? '不适用' : statusText[item.result_status] }}</b></button></nav>
      <div class="workbench-detail">
        <ReviewStepDetail v-if="current" :step="current" :page-data="pageData" :qr-checks="review.qr_checks" :custom-field-review="Boolean(modelStep)" :disabled="readOnly || state.busy || Boolean(state.blockingIssue)" :on-focus-image="focus" :on-fill="fill" :on-choose="choose">
          <template v-if="modelStep" #field-review><VehicleModelPicker :model="model" :step="current" :on-focus-image="focus" :page-data="pageData" :disabled="readOnly || state.busy || Boolean(state.blockingIssue)"/></template>
        </ReviewStepDetail>
        <div v-else-if="!current" class="workbench-empty"><strong>{{ allDone ? "当前展示项目已处理" : "当前没有待处理项目" }}</strong><p>可切换到全部字段查看完整核验记录。</p></div>
        <p v-if="state.message" class="workbench-message" role="status">{{ state.message }}</p>
      </div>
    </div>
    <div v-if="state.filledCount > 0 && onRerun && !state.blockingIssue" class="final-review-action"><span>页面已发生 {{ state.filledCount }} 项回填</span><button type="button" @click="onRerun">重新采集并最终复核</button></div>
  </section>
</template>

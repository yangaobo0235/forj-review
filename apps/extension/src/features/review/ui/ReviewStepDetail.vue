<script setup lang="ts">
import { computed } from "vue";
import ManualValueInput from "./ManualValueInput.vue";
import CandidateValue from "./CandidateValue.vue";
import QrUrl from "./QrUrl.vue";
import StructuredEvidence from "./StructuredEvidence.vue";
import { fieldLabel } from "../model/review-panel";
import { isQrTask } from "../model/review-tasks";
import { cleanPageValue, fieldFromStep, statusText, stepTitle, type Decision } from "../model/workbench";
import type { CheckResultValue, PageData, QrCheck, ReviewTask, ReasonCode } from "../../../shared/contracts/review";

const props = defineProps<{
  step: ReviewTask; pageData?: PageData | null; qrChecks: QrCheck[]; disabled: boolean;
  customFieldReview?: boolean;
  onFocusImage: (id: string) => Promise<void>;
  onFill: (step: ReviewTask, value: string) => Promise<void>;
  onChoose: (step: ReviewTask, decision: Decision) => void;
}>();
const field = computed(() => fieldFromStep(props.step));
const pageValue = computed(() => field.value ? props.pageData?.pageFields[field.value] ?? props.step.page_value : props.step.page_value);
const values = computed(() => isQrTask(props.step) || props.step.details?.not_applicable ? [] : props.step.values.filter(item =>
  item.source !== "申请页面字段" && item.value != null && String(item.value).trim() && String(item.value) !== "[object Object]"));
const images = computed(() => new Map((props.pageData?.images ?? []).filter(image => image.imageId).map(image => [image.imageId as string, image])));
const pageValues = computed(() => props.step.page_values?.length ? props.step.page_values : [{source: "页面原始值", value: pageValue.value}]);
// 规则结论只有自带页面侧取值时才展示原值，避免凭空出现一行“未采集”。
const hasPageValues = computed(() => props.step.category === "FIELD" || Boolean(props.step.page_values?.length));
function imageFor(item: CheckResultValue) {
  return (item.image_id ? images.value.get(item.image_id) : undefined) || (props.pageData?.images ?? [])
    .find(image => item.image_index != null && image.index === item.image_index);
}
const categoryLabels: Record<string, string> = {FIELD: "页面字段", BUSINESS_RULE: "业务规则", EXTERNAL: "页面外核验"};
const reasonLabels: Record<ReasonCode, string> = {
  VALUE_MISMATCH: "可靠证据存在差异", RULE_VIOLATION: "规则不满足", EVIDENCE_MISSING: "证据不足",
  RECOGNITION_UNCERTAIN: "识别不确定", AUTHORITY_MISSING: "缺少权威基准", PAGE_VALUE_MISSING: "页面值缺失",
  POLICY_REVIEW: "政策需人工确认", EXTERNAL_UNAVAILABLE: "外部核验未完成", SYSTEM_INPUT: "页面输入项，无需材料比对",
};
</script>

<template>
  <article class="workbench-card">
    <div class="workbench-card-title"><div><small>{{ categoryLabels[step.category] ?? "核验" }}</small><h2>{{ stepTitle(step) }}</h2></div><b :class="`status-${step.result_status.toLowerCase()}`">{{ step.details?.not_applicable && step.result_status === 'MATCH' ? '不适用' : statusText[step.result_status] }}</b></div>
    <div v-if="step.reason_codes?.length" class="decision-reasons"><span v-for="code in step.reason_codes" :key="code">{{ reasonLabels[code] ?? code }}</span></div>
    <div v-if="hasPageValues" class="page-value"><span>页面原始值</span><div v-for="item in pageValues" :key="item.source" class="page-value-row"><small v-if="fieldLabel(item.source) !== '页面原始值'">{{ fieldLabel(item.source) }}</small><strong>{{ cleanPageValue(item.value) || (step.details?.not_applicable ? "留空" : item.value != null ? "未填写" : "未采集") }}</strong></div><p v-if="step.details?.page_value_note" class="page-value-note">{{ step.details.page_value_note }}</p></div>
    <div v-if="!customFieldReview && values.length" class="candidate-list"><span class="evidence-section-title">材料提取值</span><CandidateValue v-for="(item, index) in values" :key="`${item.source}-${index}`" :item="item" :image="imageFor(item)" :step-label="stepTitle(step)" :status="step.result_status" :can-fill="Boolean(field) && step.result_status !== 'MATCH' && (!step.details?.permitted_fill_values || step.details.permitted_fill_values.includes(String(item.value)))" :disabled="disabled || !step.writable" :on-focus-image="onFocusImage" :on-fill="value => onFill(step, value)"/></div>
    <p v-if="!customFieldReview && !step.details?.reason_distributed" class="workbench-reason">{{ step.reason }}</p>
    <ManualValueInput v-if="field && !customFieldReview && !step.details?.not_applicable" :key="`${pageData?.collectionId}-${field}`" :initial-value="cleanPageValue(pageValue)" :label="stepTitle(step)" :disabled="disabled || !pageData || !step.writable" :on-fill="value => onFill(step, value)"/>
    <template v-if="isQrTask(step)"><QrUrl v-for="(check, index) in qrChecks" :key="`${check.image_index}-${index}`" :check="check"/></template>
    <StructuredEvidence v-if="!customFieldReview" :evidence="step.evidence"/>
    <slot name="field-review"/>
    <div v-if="step.requires_reviewer_action" class="workbench-actions"><button type="button" :disabled="disabled" class="secondary-action" @click="onChoose(step, 'MARKED_EXCEPTION')">标记人工复核</button></div>
  </article>
</template>

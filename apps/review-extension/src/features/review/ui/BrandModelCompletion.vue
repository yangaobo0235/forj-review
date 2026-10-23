<script setup lang="ts">
import { computed } from "vue";
import type { PageData, ReviewTask } from "../../../shared/contracts/review";

const props = defineProps<{
  step: ReviewTask;
  pageData?: PageData | null;
  disabled: boolean;
  onFocusImage: (id: string) => Promise<void>;
  onFill: (step: ReviewTask, value: string) => Promise<void>;
}>();
const completion = computed(() => props.step.details!.brand_model_completion!);
const canFill = computed(
  () =>
    props.step.result_status !== "MATCH" &&
    completion.value.status === "READY" &&
    Boolean(completion.value.completed_value) &&
    props.step.details?.permitted_fill_values?.includes(completion.value.completed_value),
);
const issues = computed(() => [...new Set(completion.value.issues)]);
const resultParts = computed(() => {
  const { completed_value: value, license_value: license } = completion.value;
  return license && value.startsWith(license)
    ? [license, value.slice(license.length)]
    : [value, ""];
});
const fillNote = computed(() =>
  !completion.value.completed_value
    ? "未取得组合值，可在下方人工填写"
    : props.step.result_status === "MATCH"
      ? "页面已一致，无需回填"
      : !canFill.value
        ? "当前未提供可回填组合值"
        : "请核对材料后自行决定是否回填。回填成功不直接改变核验结论。",
);
const evidence = computed(() =>
  props.step.values
    .filter((item) => !item.derived_from)
    .map((item) => ({
      ...item,
      image: props.pageData?.images.find((image) =>
        item.image_id
          ? image.imageId === item.image_id
          : item.image_index != null && image.index === item.image_index,
      ),
    })),
);
</script>

<template>
  <section
    class="brand-completion"
    aria-label="品牌型号合成核验"
  >
    <div
      class="completion-result"
      :class="{ blocked: completion.status !== 'READY' }"
    >
      <header class="material-value-heading">
        <span>材料组合值</span
        ><button
          class="fill-value-button"
          type="button"
          :disabled="!canFill || disabled || !step.writable || !pageData"
          :title="fillNote"
          aria-label="品牌型号回填材料值"
          @click="onFill(step, completion.completed_value)"
        >
          回填此值
        </button>
      </header>
      <strong
        v-if="completion.completed_value"
        class="completion-code"
        >{{ resultParts[0] }}<span class="completion-suffix">{{ resultParts[1] }}</span></strong
      >
      <strong v-else>暂不能确定完整品牌型号</strong>
      <p v-if="completion.completed_value">{{ completion.basis }}</p>
      <p v-else>未取得可用的完整组合值，请核对下方材料读值与原图。</p>
      <div
        v-if="issues.length"
        class="completion-warning"
        role="note"
        aria-label="材料核对提示"
      >
        <p
          v-for="issue in issues"
          :key="issue"
        >
          {{ issue }}
        </p>
      </div>
      <div
        class="completion-materials"
        aria-label="材料依据与原图"
      >
        <div
          v-for="(item, index) in evidence"
          :key="index"
          class="completion-evidence"
        >
          <small
            >{{ item.source
            }}{{
              item.document_type === "vehicle_license"
                ? " · 品牌型号"
                : ["registration_certificate", "vehicle_nameplate"].includes(
                      item.document_type ?? "",
                    )
                  ? " · 整车型号"
                  : ""
            }}</small
          >
          <strong class="completion-code">{{ item.value || "未取得读值" }}</strong>
          <p
            v-if="item.uncertain"
            class="completion-uncertain"
          >
            识别不确定，请核对原图
          </p>
          <div
            v-if="item.image"
            class="completion-image"
          >
            <img
              :src="item.image.src"
              :alt="item.image.alt || `${item.source}原图`"
            />
            <button
              v-if="item.image.imageId"
              type="button"
              class="image-action"
              @click="onFocusImage(item.image.imageId)"
            >
              查看原图
            </button>
          </div>
          <small v-else>未关联到材料原图，请在业务页面核对。</small>
        </div>
        <p
          v-if="!evidence.length"
          class="completion-note"
        >
          尚未取得材料读值，请核对上传材料及识别状态。
        </p>
      </div>
      <p
        v-if="completion.status === 'READY'"
        class="completion-note"
      >
        {{ step.reason }}
      </p>
      <p class="completion-note">{{ fillNote }}</p>
    </div>
  </section>
</template>

<style scoped>
.brand-completion {
  display: grid;
  gap: 14px;
  min-width: 0;
  font-size: var(--review-font-body);
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.brand-completion > *,
.completion-evidence > * {
  min-width: 0;
}
small,
.completion-note {
  color: var(--review-muted);
  font-size: var(--review-font-meta);
}
.completion-result {
  padding: 12px;
  background: var(--review-accent-surface);
  border: 1px solid var(--review-accent-border);
  border-radius: 8px;
}
.completion-result.blocked {
  background: var(--review-subtle);
}
strong {
  display: block;
  font-weight: 500;
}
.completion-code {
  font-family: Consolas, "Microsoft YaHei", monospace;
  overflow-wrap: anywhere;
  word-break: normal;
}
.completion-result > strong {
  font-size: var(--review-font-section);
  margin: 8px 0 4px;
}
.completion-suffix {
  color: var(--review-primary);
}
p {
  margin: 6px 0 0;
}
.completion-warning,
.completion-uncertain {
  color: var(--review-warning);
  font-size: var(--review-font-meta);
}
.completion-warning {
  border-left: 3px solid var(--review-warning);
  padding: 4px 10px;
  margin-top: 10px;
  background: var(--review-subtle);
}
.completion-warning p:first-child {
  margin-top: 0;
}
.completion-materials {
  border-top: 1px solid var(--review-accent-border);
  margin-top: 12px;
}
.completion-evidence {
  padding-top: 12px;
}
.completion-evidence + .completion-evidence {
  border-top: 1px solid var(--review-accent-border);
  margin-top: 12px;
}
.completion-evidence > small {
  display: block;
  margin-bottom: 4px;
}
.completion-image {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.completion-image img {
  display: block;
  width: 88px;
  max-width: 100%;
  height: 64px;
  object-fit: contain;
  background: var(--review-surface);
  border: 1px solid var(--review-border);
  border-radius: 6px;
}
button {
  width: auto;
  max-width: 100%;
  border: 1px solid var(--review-primary);
  border-radius: 7px;
  background: var(--review-primary);
  color: #fff;
  padding: 6px 10px;
  cursor: pointer;
  font: inherit;
  white-space: nowrap;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
button.image-action {
  background: transparent;
  color: var(--review-primary);
  border-color: transparent;
  padding: 3px 0;
}
</style>

<script setup lang="ts">
import { computed } from "vue";
import type { PageData, ReviewJobSnapshot } from "../../../shared/contracts/review";
const props = defineProps<{
  job: ReviewJobSnapshot;
  pageData?: PageData | null;
  onFocusImage?: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
}>();
const processed = computed(
  () =>
    props.job.progress.completed_count +
    props.job.progress.failed_count +
    props.job.progress.timed_out_count,
);
const percent = computed(() =>
  props.job.progress.total_count
    ? Math.min(100, Math.round((processed.value / props.job.progress.total_count) * 100))
    : ["FINALIZING", "COMPLETED"].includes(props.job.phase)
      ? 100
      : 0,
);
const phaseText = computed(
  () =>
    ({
      PREPARING: "正在准备审核任务",
      RECOGNIZING: "正在识别图片",
      FINALIZING: "正在生成审核结论",
      COMPLETED: props.job.status === "PARTIAL" ? "审核部分完成，需人工复核" : "审核已完成",
      FAILED: "审核失败",
      CANCELLED: "审核已取消",
    })[props.job.phase],
);
const failedIds = computed(() => new Set(props.job.failed_image_ids ?? []));
const timedOutIds = computed(() => new Set(props.job.timed_out_image_ids ?? []));
const affectedImages = computed(() =>
  (props.pageData?.images ?? []).filter(
    (image) =>
      image.collectionError ||
      failedIds.value.has(String(image.imageId || image.index)) ||
      timedOutIds.value.has(String(image.imageId || image.index)),
  ),
);
const finished = computed(() => ["COMPLETED", "FAILED", "CANCELLED"].includes(props.job.phase));
</script>

<template>
  <div
    class="review-progress"
    :class="{ 'is-finished': finished }"
  >
    <div class="review-progress-heading">
      <strong>审核进度</strong><span>{{ phaseText }}</span>
    </div>
    <div
      v-if="!finished"
      class="review-progress-track"
      role="progressbar"
      aria-label="审核图片处理进度"
      :aria-valuemin="0"
      :aria-valuemax="100"
      :aria-valuenow="percent"
    >
      <div
        class="review-progress-bar"
        :style="{ width: `${percent}%` }"
      />
    </div>
    <small class="review-progress-counts"
      >图片已处理 {{ processed }}/{{ job.progress.total_count }} · 识别成功
      {{ job.progress.completed_count }} 张</small
    >
    <template v-if="job.progress.failed_count > 0 || job.progress.timed_out_count > 0">
      <small
        >失败 {{ job.progress.failed_count }} 张，超时 {{ job.progress.timed_out_count }} 张</small
      >
      <small>请刷新原页面后重新审核；仍未完成的材料请查看原图人工核对。</small>
    </template>
    <ul
      v-if="affectedImages.length"
      class="review-progress-failures"
      aria-label="未完成识别的材料"
    >
      <li
        v-for="image in affectedImages"
        :key="String(image.imageId || image.index)"
      >
        <span
          >第 {{ image.index + 1 }} 张 ·
          {{ image.alt || image.groupTitle || image.group || "材料" }}：{{
            image.collectionError
              ? "暂时无法读取"
              : timedOutIds.has(String(image.imageId || image.index))
                ? "识别超时"
                : "识别失败"
          }}</span
        >
        <button
          v-if="image.imageId && onFocusImage"
          type="button"
          class="evidence-focus"
          @click="onFocusImage(image.imageId)"
        >
          查看原图
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.review-progress {
  display: grid;
  gap: 8px;
  padding: 0;
  color: var(--review-secondary);
  font-size: var(--review-font-meta);
}

.review-progress-heading {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 8px;
  min-width: 0;
  color: var(--review-secondary);
  font-size: var(--review-font-body);
}

.review-progress-heading span {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

.review-progress-track {
  height: 4px;
  overflow: hidden;
  border-radius: 2px;
  background: #d9ecff;
}

.review-progress-bar {
  height: 100%;
  border-radius: inherit;
  background: var(--review-primary);
  transition: width 240ms ease;
}

.review-progress-failures {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--review-font-meta);
}

.review-progress-failures li {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.review-progress-failures span {
  min-width: 0;
  overflow-wrap: anywhere;
}

.review-progress-heading strong {
  font-weight: 400;
  color: var(--review-secondary);
  font-size: var(--review-font-meta);
}
.review-progress.is-finished {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 6px 12px;
}
.review-progress-counts {
  color: var(--review-muted);
  font-size: var(--review-font-meta);
  font-variant-numeric: tabular-nums;
}
.review-progress.is-finished > :not(.review-progress-heading, .review-progress-counts) {
  grid-column: 1 / -1;
}
@media (max-width: 420px) {
  .review-progress.is-finished {
    grid-template-columns: minmax(0, 1fr);
  }
}

.review-progress > small {
  font-size: var(--review-font-meta);
  line-height: 1.6;
}

.review-progress-failures button {
  flex-shrink: 0;
  min-height: 32px;
  padding: 5px 8px;
  border: 1px solid var(--review-border);
  border-radius: 7px;
  background: var(--review-surface);
  color: var(--review-primary);
  font-size: var(--review-font-meta);
  white-space: nowrap;
  cursor: pointer;
}
</style>

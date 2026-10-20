<script setup lang="ts">
import { computed } from "vue";
import type { ReviewResultsProps } from "./review-result-props";
import { exceptionComparisons, exceptionSections } from "../presentation/exception";
import ReviewTaskList from "./ReviewTaskList.vue";
import ReviewAdvice from "./ReviewAdvice.vue";
import QrResults from "./QrResults.vue";
import ResultGroup from "./ResultGroup.vue";
const props = defineProps<ReviewResultsProps>();
const filters = ["ALL", "CONFLICT", "REVIEW_REQUIRED"] as const;
const imagesById = computed(() => new Map((props.pageData?.images || []).filter(image => image.imageId).map(image => [image.imageId as string, image])));
const exceptionGroups = computed(() => exceptionSections(props.review.sections, props.review.comparisons));
const exceptionItems = computed(() => exceptionComparisons(props.review.sections, props.review.comparisons, props.exceptionFilter));
const exceptionCounts = computed(() => ({ ALL: exceptionComparisons(props.review.sections, props.review.comparisons).length, CONFLICT: exceptionComparisons(props.review.sections, props.review.comparisons, "CONFLICT").length, REVIEW_REQUIRED: exceptionComparisons(props.review.sections, props.review.comparisons, "REVIEW_REQUIRED").length }));
const visibleGroups = computed(() => exceptionGroups.value.map(group => ({ ...group, comparisons: group.comparisons.filter(comparison => props.exceptionFilter === "ALL" || comparison.status === props.exceptionFilter) })).filter(group => group.comparisons.length > 0));
</script>

<template>
  <section class="review-result">
    <ReviewTaskList :tasks="review.review_tasks || []" /><ReviewAdvice :review="review" />
    <QrResults v-if="review.business_type === 'scrap_replacement'" :review="review" />
    <section class="result-card exception-card">
      <div class="group-heading"><h2>需要处理</h2><small>{{ exceptionCounts.ALL }} 项</small></div>
      <div v-if="exceptionCounts.ALL > 0" class="exception-filters" role="tablist" aria-label="异常筛选">
        <button v-for="filter in filters" :key="filter" :class="exceptionFilter === filter ? 'filter-button active' : 'filter-button'" type="button" @click="onExceptionFilterChange(filter)">{{ filter === "ALL" ? "全部异常" : filter === "CONFLICT" ? "冲突" : "待复核" }} ({{ exceptionCounts[filter] }})</button>
      </div>
      <template v-if="exceptionItems.length > 0"><ResultGroup v-for="group in visibleGroups" :key="group.section.id" :title="group.section.title" :group-status="job?.groups[group.section.id]?.status" :comparisons="group.comparisons" :images-by-id="imagesById" :on-focus-image="onFocusImage" /></template>
      <p v-else class="field-check-empty">审核通过，字段校验未发现冲突或待复核项</p>
    </section>
    <section v-if="review.sections.length === 0 && review.issues.length > 0" class="result-card"><h2>业务处理状态</h2><p v-for="issue in review.issues" :key="issue" class="muted">{{ issue }}</p></section>
  </section>
</template>

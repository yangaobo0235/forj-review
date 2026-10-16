<script setup lang="ts">
import { computed } from "vue";
import { resolveWorkbenchRenderer } from "./workbench-renderers";
import type { ReviewResultsProps } from "./review-result-props";
import LegacyReviewResults from "./LegacyReviewResults.vue";
const props = defineProps<ReviewResultsProps>();
const renderer = computed(() => resolveWorkbenchRenderer(props.review));
const workbenchProps = computed(() => ({ review: props.review, pageData: props.pageData, onFocusImage: props.onFocusImage, onApplyPageFieldValue: props.onApplyPageFieldValue, onApplyPageFieldGroupValue: props.onApplyPageFieldGroupValue, onRerun: props.onRerun }));
</script>

<template>
  <component :is="renderer" v-if="renderer" v-bind="workbenchProps" />
  <LegacyReviewResults v-else v-bind="props" />
</template>

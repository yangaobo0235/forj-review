<script setup lang="ts">
import ReviewWorkbench from "./ReviewWorkbench.vue";
import type { WorkbenchProps } from "./workbench-props";

defineProps<WorkbenchProps>();
</script>

<template>
  <ReviewWorkbench
    v-if="Array.isArray(review.review_tasks)"
    :key="pageData?.collectionId"
    v-bind="$props"
  />
  <p
    v-else
    class="error-message"
    role="alert"
  >
    审核服务未返回有效的字段任务，请重新审核。
  </p>
</template>

<script setup lang="ts">
import ReviewWorkbench from "./ReviewWorkbench.vue";
import type { WorkbenchProps } from "./workbench-props";
defineProps<WorkbenchProps>();
</script>

<template>
  <ReviewWorkbench :key="pageData?.collectionId" v-bind="$props"/>
</template>

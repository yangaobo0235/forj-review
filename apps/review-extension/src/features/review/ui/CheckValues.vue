<script setup lang="ts">
import type { CheckResult } from "../../../shared/contracts/review";
defineProps<{ check: CheckResult }>();
</script>

<template>
  <dl v-if="check.values?.length" class="advice-values">
    <div v-for="(item, index) in check.values" :key="`${item.source}-${index}`"><dt>{{ item.source }}</dt><dd>{{ item.value == null || item.value === "" ? "未取得" : String(item.value) }}</dd></div>
  </dl>
</template>

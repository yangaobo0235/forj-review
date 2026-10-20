<script setup lang="ts">
import { computed } from "vue";
const props = withDefaults(defineProps<{text: string; pending?: boolean}>(), {pending: false});
// 只标记说明文字里的“不一致”，不据此改变任何审核状态。
const parts = computed(() => props.text.split("不一致"));
</script>

<template>
  <template v-for="(part, index) in parts" :key="index"><mark v-if="index" :class="pending ? 'mismatch-pending' : 'mismatch'">不一致</mark>{{ part }}</template>
</template>

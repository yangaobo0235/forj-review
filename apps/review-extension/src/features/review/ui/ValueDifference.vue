<script setup lang="ts">
import { computed } from "vue";
import type { DifferenceRange } from "../../../shared/contracts/review";
const props = withDefaults(
  defineProps<{ value: unknown; differences?: DifferenceRange[]; pending?: boolean }>(),
  { differences: () => [], pending: false },
);
// 差异区间由后端提供；末尾空字符保留“缺少页面字符”的展示位置。
const parts = computed(() =>
  [...String(props.value ?? ""), ""].map((char, index) => ({
    char,
    missing: props.differences.filter((range) => range.kind === "MISSING" && range.start === index),
    changed: props.differences.some((range) => range.start <= index && index < range.end),
  })),
);
</script>

<template>
  <span
    v-for="(part, index) in parts"
    :key="index"
    ><mark
      v-for="(range, i) in part.missing"
      :key="i"
      :class="pending ? 'value-diff-pending' : 'value-diff'"
      :title="`缺少页面字符：${range.page_text}`"
      >[缺少 {{ range.page_text }}]</mark
    ><mark
      v-if="part.changed"
      :class="pending ? 'value-diff-pending' : 'value-diff'"
      >{{ part.char }}</mark
    ><template v-else>{{ part.char }}</template></span
  >
</template>

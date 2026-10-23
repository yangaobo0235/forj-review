<script setup lang="ts">
const iconPaths = {
  arrow: "M5 12h14m-6-6 6 6-6 6",
  chevron: "m9 5 7 7-7 7",
  fileCheck: "M14 3H5v18h14V8l-5-5Zm0 0v6h5m-11 6 3 3 5-6",
  file: "M14 3H5v18h14V8l-5-5Zm0 0v6h5M8 13h8m-8 4h6",
  external: "M14 3h7v7m0-7L10 14M9 3H3v18h18v-6",
  shield: "m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6l8-3Zm-4 9 3 3 5-5",
  clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
};

defineOptions({ name: "ReviewIcon" });
withDefaults(defineProps<{ name: keyof typeof iconPaths; size?: number }>(), { size: 20 });
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="iconPaths[name]" />
  </svg>
</template>

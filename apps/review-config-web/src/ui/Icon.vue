<script setup lang="ts">
const iconPaths = {
  plus: "M12 5v14M5 12h14", arrow: "M5 12h14m-6-6 6 6-6 6", send: "m5 12 14-7-4 14-4-5-6-2Zm6 2 8-9",
  chat: "M20 11a8 8 0 0 1-8 8H5l-3 3V11a9 9 0 0 1 18 0Z",
  check: "m5 12 4 4L19 6", close: "m6 6 12 12M6 18 18 6", chevron: "m9 5 7 7-7 7",
  scan: "M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 12h18",
  pageFields: "M3 4h18v16H3V4Zm0 5h18M8 9v11m4-7h5m-5 4h5",
  file: "M14 3H5v18h14V8l-5-5Zm0 0v6h5M8 13h8m-8 4h6",
  image: "M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M15 7h.01",
  refresh: "M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3",
  settings: "M4 7h16M4 17h16M8 4v6m8 4v6", external: "M14 3h7v7m0-7L10 14M9 3H3v18h18v-6",
  shield: "m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6l8-3Zm-4 9 3 3 5-5",
  trash: "M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7",
  pause: "M8 5v14M16 5v14", clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
};

defineOptions({ name: "ConfigurationIcon" });
withDefaults(defineProps<{ name: keyof typeof iconPaths; size?: number }>(), { size: 20 });
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="iconPaths[name]" /></svg>
</template>

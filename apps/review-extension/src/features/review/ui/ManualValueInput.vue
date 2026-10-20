<script setup lang="ts">
import { ref } from "vue";
const props = defineProps<{initialValue: string; label: string; disabled: boolean; onFill: (value: string) => Promise<void>}>();
const value = ref(props.initialValue);
</script>

<template>
  <div class="manual-fill"><label><span>人工输入值</span><input v-model="value" :aria-label="`${label}人工输入值`" :disabled="disabled"/></label><button type="button" class="fill-value-button" :disabled="disabled || !value.trim()" @click="onFill(value.trim())">回填此值</button></div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { ReviewResponse } from "../../../shared/contracts/review";
import { qrCheckPresentation } from "../presentation/qr";
const props = defineProps<{ review: ReviewResponse }>();
const checks = computed(() => props.review.qr_checks.map((check, index) => ({ key: `${check.image_index}-${index}`, ...qrCheckPresentation(check) })));
</script>

<template>
  <section class="result-card"><h2>二维码核验</h2><p v-if="!checks.length" class="muted">暂无二维码核验结果</p>
    <details v-for="presentation in checks" :key="presentation.key" :class="`qr-check status-${presentation.visualStatus.toLowerCase()}`">
      <summary class="qr-summary"><span>商务部官网</span><strong>{{ presentation.title }}</strong></summary>
      <div class="qr-detail"><dl v-if="presentation.fields.length" class="qr-fields"><div v-for="field in presentation.fields" :key="field.label"><dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></div></dl><small>{{ presentation.message }}</small></div>
    </details>
  </section>
</template>

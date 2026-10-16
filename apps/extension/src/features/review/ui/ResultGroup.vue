<script setup lang="ts">
import { computed } from "vue";
import { comparisonEvidence, evidenceHighlightPlans } from "../presentation/evidence";
import { fieldLabel, groupStatusLabel, statusLabel } from "../model/review-panel";
import type { FieldComparison, JobStatus, PageData } from "../../../shared/contracts/review";
import EvidenceSource from "./EvidenceSource.vue";
const props = defineProps<{ title: string; comparisons: FieldComparison[]; groupStatus?: JobStatus; imagesById: Map<string, PageData["images"][number]>; onFocusImage: (imageId: string) => Promise<{ ok: boolean; error?: string }> }>();
const rows = computed(() => props.comparisons.map(comparison => {
  const sourceValues = comparisonEvidence(comparison.evidence, comparison.right_value);
  return { comparison, sourceValues, highlightPlans: evidenceHighlightPlans(sourceValues), compareByPosition: comparison.field.endsWith(".vin") || comparison.field.endsWith(".plate_no") };
}));
</script>

<template>
  <section class="result-card">
    <div class="group-heading"><h2>{{ title }}</h2><small>{{ groupStatusLabel(groupStatus) }}</small></div>
    <p v-if="!comparisons.length" class="muted">暂无可比较字段，需接入识别工具后复核</p>
    <div v-for="{ comparison, sourceValues, highlightPlans, compareByPosition } in rows" :key="comparison.field" :class="`check-row status-${comparison.status.toLowerCase()}`">
      <span>{{ fieldLabel(comparison.field) }}</span><strong>{{ statusLabel(comparison.status) }}</strong>
      <div class="comparison-values source-values"><EvidenceSource v-for="(evidence, index) in sourceValues" :key="`${evidence.detail || evidence.source}-${index}`" :evidence="evidence" :highlight-plan="highlightPlans[index]!" :compare-by-position="compareByPosition" :image="evidence.image_id ? imagesById.get(evidence.image_id) : undefined" compact :on-focus-image="onFocusImage" /></div>
    </div>
  </section>
</template>

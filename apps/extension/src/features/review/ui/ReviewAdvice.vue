<script setup lang="ts">
import { computed } from "vue";
import { checkStatusLabel, orderedCrossChecks, recommendationVisual } from "../presentation/advice";
import type { ReviewResponse } from "../../../shared/contracts/review";
import { isMaterialDiagnostic } from "../model/review-tasks";
import CheckValues from "./CheckValues.vue";
const props = defineProps<{ review: ReviewResponse }>();
const advice = computed(() => props.review.agent_advice);
const findings = computed(() => (advice.value?.findings || []).filter(check => !isMaterialDiagnostic(check.check_id)));
const crossChecks = computed(() => orderedCrossChecks(props.review.business_type, props.review.cross_checks || []).filter(check => !isMaterialDiagnostic(check.check_id)));
const visual = computed(() => recommendationVisual(props.review.recommendation));
</script>

<template>
  <section :class="`recommendation recommendation-${review.recommendation.toLowerCase()}`">
    <div class="recommendation-header">
      <span class="recommendation-icon" role="img" :aria-label="visual.label">{{ visual.symbol }}</span>
      <div class="recommendation-copy"><span class="recommendation-eyebrow">Agent 最终审核建议</span><strong class="recommendation-title">{{ advice?.title || "建议人工复核" }}</strong><p class="recommendation-summary">{{ advice?.summary || "审核建议尚未完整生成" }}</p></div>
    </div>
    <details v-if="findings.length" class="advice-section">
      <summary><span class="advice-section-label">需复核项目</span><span class="advice-section-count">{{ findings.length }}</span><span class="advice-section-chevron" aria-hidden="true">⌄</span></summary>
      <div class="advice-findings"><article v-for="finding in findings" :key="finding.check_id" class="advice-finding"><div class="advice-check-heading"><strong>{{ finding.label }}</strong><b>{{ checkStatusLabel(finding.status) }}</b></div><p>{{ finding.reason }}</p><CheckValues :check="finding" /></article></div>
    </details>
    <details v-if="crossChecks.length" class="advice-section" :open="crossChecks.some(check => check.status !== 'MATCH')">
      <summary><span class="advice-section-label">跨资料校验</span><span class="advice-section-chevron" aria-hidden="true">⌄</span></summary>
      <div class="cross-checks"><article v-for="check in crossChecks" :key="check.check_id" :class="`cross-check cross-check-${check.status.toLowerCase()}`"><div class="advice-check-heading"><strong>{{ check.label }}</strong><b>{{ checkStatusLabel(check.status) }}</b></div><p>{{ check.reason }}</p><CheckValues :check="check" /></article></div>
    </details>
  </section>
</template>

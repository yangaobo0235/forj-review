<script setup lang="ts">
import { computed } from "vue";
import { evidencePresentation, type EvidenceHighlightPlan } from "../presentation/evidence";
import { diffValue, diffValueByPosition } from "../presentation/value-diff";
import type { EvidenceFact, PageData } from "../../../shared/contracts/review";
const props = withDefaults(defineProps<{ evidence: EvidenceFact; highlightPlan: EvidenceHighlightPlan; compareByPosition?: boolean; image?: PageData["images"][number]; compact?: boolean; onFocusImage: (imageId: string) => Promise<{ ok: boolean; error?: string }> }>(), { compareByPosition: false, compact: false });
const thumbnail = computed(() => props.compact ? undefined : props.image?.src);
const presentation = computed(() => evidencePresentation(props.evidence));
const className = computed(() => `evidence-source ${presentation.value.kind}-evidence${props.evidence.conflicting ? " evidence-conflicting" : ""}${presentation.value.kind === "image" && thumbnail.value ? " has-thumbnail" : ""}`);
const parts = computed(() => props.highlightPlan.markAll ? [{ text: presentation.value.value, changed: Boolean(presentation.value.value) }] : (props.compareByPosition ? diffValueByPosition : diffValue)(presentation.value.value, props.highlightPlan.compareTo));
const differenceTitle = computed(() => props.highlightPlan.markAll ? "未形成多数值" : "与基准值不一致");
</script>

<template>
  <div :class="className">
    <img v-if="thumbnail" :src="thumbnail" :alt="image?.alt || '审核资料缩略图'" /><b class="evidence-label">{{ presentation.label }}</b>
    <span class="evidence-value"><template v-for="(part, index) in parts" :key="index"><mark v-if="part.changed" :title="differenceTitle">{{ part.text }}</mark><span v-else>{{ part.text }}</span></template></span>
    <button v-if="evidence.image_id" class="evidence-focus" type="button" @click="onFocusImage(evidence.image_id)">查看原图</button>
  </div>
</template>

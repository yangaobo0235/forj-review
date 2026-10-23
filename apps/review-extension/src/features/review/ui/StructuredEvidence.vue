<script setup lang="ts">
import { computed } from "vue";
import type { EvidenceFact } from "../../../shared/contracts/review";
const props = defineProps<{ evidence: EvidenceFact[] }>();
const rows = computed(() =>
  props.evidence.flatMap((item) =>
    item.value && typeof item.value === "object" && !Array.isArray(item.value)
      ? Object.entries(item.value as Record<string, unknown>)
          .filter(([, value]) => value != null && value !== "")
          .map(([key, value]) => ({ source: item.source, key, value: String(value) }))
      : item.field?.startsWith("business_license.") && item.value != null && item.value !== ""
        ? [{ source: item.source, key: item.field, value: String(item.value) }]
        : [],
  ),
);
const fieldLabels: Record<string, string> = {
  company_name: "企业名称",
  "business_license.company_name": "企业名称",
  legal_representative: "法定代表人",
  "business_license.legal_representative": "法定代表人",
  unified_social_credit_code: "统一社会信用代码",
  "business_license.unified_social_credit_code": "统一社会信用代码",
  vin: "车架号",
  certificate_no: "证明编号",
};
</script>

<template>
  <dl
    v-if="rows.length"
    class="structured-evidence"
  >
    <div
      v-for="(row, index) in rows"
      :key="`${row.source}-${row.key}-${index}`"
    >
      <dt>{{ row.source }} · {{ fieldLabels[row.key] ?? row.key }}</dt>
      <dd>{{ row.value }}</dd>
    </div>
  </dl>
</template>

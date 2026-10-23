<script setup lang="ts">
import { computed } from "vue";
import { evidencePresentation } from "../presentation/evidence";
import type {
  CheckResultValue,
  CheckResultStatus,
  PageImage,
} from "../../../shared/contracts/review";
import ValueDifference from "./ValueDifference.vue";
import MismatchText from "./MismatchText.vue";

const props = defineProps<{
  item: CheckResultValue;
  image?: PageImage;
  stepLabel: string;
  status: CheckResultStatus;
  canFill: boolean;
  disabled: boolean;
  onFocusImage: (id: string) => Promise<void>;
  onFill: (value: string) => Promise<void>;
}>();
const label = computed(() =>
  props.item.source === "图片识别" ? evidencePresentation(props.item).label : props.item.source,
);
// 消费后端逐值结论；兼容旧协议的显示兜底，不在前端重新比较取值。
const comparisonStatus = computed(
  () =>
    props.item.comparison_status ??
    (props.item.uncertain || props.status === "INSUFFICIENT"
      ? "INSUFFICIENT"
      : props.item.conflicting === false
        ? "MATCH"
        : props.status),
);
const pending = computed(() => comparisonStatus.value === "INSUFFICIENT");
const comparison = computed(() =>
  pending.value
    ? "比对结论待确认"
    : props.item.conflicting == null && !props.item.comparison_status
      ? ""
      : comparisonStatus.value === "CONFLICT"
        ? "与页面不一致"
        : "与页面一致",
);
</script>

<template>
  <section class="candidate">
    <header class="material-value-heading">
      <span>{{ item.fill_role === "RECOMMENDATION" ? "系统推荐结论" : "材料核对值" }}</span
      ><button
        v-if="canFill"
        :aria-label="`${stepLabel}回填材料值`"
        class="fill-value-button"
        type="button"
        :disabled="disabled"
        @click="onFill(String(item.value))"
      >
        回填此值
      </button>
    </header>
    <div class="candidate-value">
      <strong
        ><ValueDifference
          :value="item.value"
          :differences="item.differences"
          :pending="pending"
      /></strong>
      <small v-if="!image || comparison || item.derived_from === 'invoice.invoice_no'"
        ><template v-if="!image"
          >{{ label }}{{ item.group_title ? ` · ${item.group_title}` : "" }}</template
        ><template v-if="comparison"
          >{{ !image ? " · " : ""
          }}<MismatchText
            :text="comparison"
            :pending="pending" /></template
        >{{ item.derived_from === "invoice.invoice_no" ? " · 由发票数电号码适配" : "" }}</small
      >
      <span
        v-if="item.uncertain"
        class="candidate-uncertainty"
        >识别不确定，请核对原图</span
      >
      <span
        v-if="item.fill_role === 'RECOMMENDATION' && item.derived_from"
        class="candidate-basis"
        >依据：{{ item.derived_from }}</span
      >
      <span
        v-else-if="item.check_reason"
        class="candidate-basis"
        ><MismatchText
          :text="item.check_reason"
          :pending="pending"
      /></span>
    </div>
    <div
      v-if="image"
      class="candidate-evidence"
    >
      <img
        :src="image.src"
        :alt="image.alt || `${label}材料`"
      />
      <div>
        <small>{{ label }}{{ item.group_title ? ` · ${item.group_title}` : "" }}</small
        ><button
          v-if="image.imageId"
          type="button"
          @click="onFocusImage(image.imageId)"
        >
          查看原图
        </button>
      </div>
    </div>
  </section>
</template>

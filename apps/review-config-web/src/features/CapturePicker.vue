<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Workbench } from "../model/use-workbench";
import { displayTime } from "../model/presentation";
import CaptureEvidence from "./CaptureEvidence.vue";
const { work } = defineProps<{ work: Workbench }>();
const filter = ref("");
const choosing = ref(!work.capture);
watch(
  () => work.capture?.capture_id,
  (id) => {
    if (id) choosing.value = false;
  },
);
const captures = computed(() =>
  work.captures.filter((item) =>
    `${item.title} ${item.page_id}`.toLowerCase().includes(filter.value.toLowerCase()),
  ),
);
</script>

<template>
  <section
    class="capture-picker"
    aria-label="选择页面采集"
  >
    <div class="panel-heading">
      <strong>页面现场</strong
      ><button
        v-if="work.capture"
        type="button"
        class="text-button"
        :disabled="Boolean(work.busy)"
        @click="choosing = !choosing"
      >
        {{ choosing ? "收起选择" : "选择其他现场" }}
      </button>
    </div>
    <div v-if="choosing || !work.capture">
      <div class="capture-guide">
        <strong>从业务页面新采集</strong>
        <p>在审核详情页展开相关材料，点击审核扩展中的“配置页面”。保存后会自动带入这里。</p>
      </div>
      <label
        >查找历史采集<input
          v-model="filter"
          placeholder="输入审核页面或地区"
          aria-label="查找历史采集"
      /></label>
      <label
        >历史采集<select
          :value="work.capture?.capture_id || ''"
          :disabled="Boolean(work.busy)"
          aria-label="历史采集"
          @change="
            work.run('读取历史现场', () =>
              work.selectCapture(($event.target as HTMLSelectElement).value),
            )
          "
        >
          <option
            value=""
            disabled
          >
            选择一份历史采集，或从业务页面新采集
          </option>
          <option
            v-for="item in captures"
            :key="item.capture_id"
            :value="item.capture_id"
          >
            {{ item.title }} · {{ item.page_id || "页面待确认" }} ·
            {{ displayTime(item.created_at) }} · {{ item.field_count }} 字段 /
            {{ item.sample_count }} 个图片来源
          </option>
        </select></label
      >
      <div
        v-if="!captures.length"
        class="capture-empty"
      >
        <strong>{{ filter ? "没有匹配的采集" : "暂无历史采集" }}</strong>
        <p>
          {{
            filter
              ? "试试其他页面名称，或从业务页面重新采集。"
              : "完成首次采集后，可在这里重复使用已保存的页面资料。"
          }}
        </p>
      </div>
      <button
        type="button"
        class="text-button"
        :disabled="Boolean(work.busy)"
        @click="work.run('刷新采集记录', work.inventory)"
      >
        刷新历史采集
      </button>
    </div>
    <template v-if="work.capture">
      <CaptureEvidence :capture="work.capture" />
    </template>
  </section>
</template>

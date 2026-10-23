<script setup lang="ts">
import { computed, ref } from "vue";
import type { CapturedPage } from "@forj/configuration-contracts";
import { displayTime } from "../model/presentation";
import ImageViewer from "./ImageViewer.vue";
const { capture, quotable = false } = defineProps<{ capture: CapturedPage; quotable?: boolean }>();
const emit = defineEmits<{ reference: [kind: "field" | "sample", id: string, label: string] }>();
const pane = ref("fields"),
  search = ref(""),
  viewer = ref<number | null>(null);
const groups = computed(() => {
  const result = new Map<string, typeof capture.snapshot.fields>();
  for (const field of capture.snapshot.fields) {
    const group = field.group_label || field.section || "分区未确认";
    if (!`${field.label} ${group}`.toLowerCase().includes(search.value.toLowerCase())) continue;
    if (!result.has(group)) result.set(group, []);
    result.get(group)!.push(field);
  }
  return result;
});
const failed = ref(new Set<string>());
function fail(id: string) {
  failed.value = new Set([...failed.value, id]);
}
</script>
<template>
  <section
    class="capture-evidence"
    aria-label="页面资料目录"
  >
    <strong>{{ capture.snapshot.title || "审核页面现场" }}</strong>
    <p class="muted small">
      采集于 {{ displayTime(capture.created_at) }} · {{ capture.snapshot.fields.length }} 字段 ·
      {{ capture.snapshot.samples.length }} 图片来源
    </p>
    <p class="muted small">
      {{ capture.snapshot.detected_page_id || "页面类型待确认" }} · 采集时配置
      {{ capture.snapshot.configuration_version || "未观察到" }}
    </p>
    <details>
      <summary>页面来源与记录身份</summary>
      <p class="capture-source">{{ capture.snapshot.page_url }}</p>
      <p
        v-for="(value, key) in capture.snapshot.record_identity"
        :key="key"
      >
        {{ key }}：{{ value }}
      </p>
      <p
        v-if="!Object.keys(capture.snapshot.record_identity || {}).length"
        class="muted"
      >
        未单独确认记录身份；发布前仍核对指定业务页面。
      </p>
    </details>
    <details v-if="capture.snapshot.diagnostics.length">
      <summary>采集范围提示 {{ capture.snapshot.diagnostics.length }} 项</summary>
      <p
        v-for="problem in capture.snapshot.diagnostics"
        :key="problem"
        class="blocker"
      >
        {{ problem }}
      </p>
    </details>
    <p class="muted small">资料是调查依据，无需全选。引用不代表修改范围、材料必传或回填授权。</p>
    <div
      class="subtabs"
      aria-label="资料类型"
    >
      <button
        :aria-pressed="pane === 'fields'"
        @click="pane = 'fields'"
      >
        字段 {{ capture.snapshot.fields.length }}
      </button>
      <button
        :aria-pressed="pane === 'images'"
        @click="pane = 'images'"
      >
        图片 {{ capture.snapshot.samples.length }}
      </button>
    </div>
    <template v-if="pane === 'fields'">
      <input
        v-model="search"
        aria-label="查找资料字段"
        placeholder="按名称或分区查找"
      />
      <section
        v-for="[group, fields] in groups"
        :key="group"
        class="evidence-group"
      >
        <h3>{{ group }}</h3>
        <article
          v-for="field in fields"
          :key="field.candidate_id"
          class="reference-row"
        >
          <div>
            <strong>{{ field.label || "无标签字段" }}</strong>
            <p class="original-value">{{ field.value || "空值" }}</p>
            <details>
              <summary>控件与定位</summary>
              <p class="small">
                {{ field.control_kind }} · {{ field.editable ? "页面可编辑" : "页面只读" }}
              </p>
              <code>{{ field.locator_id || field.locator_name || field.candidate_id }}</code>
            </details>
          </div>
          <button
            v-if="quotable"
            class="text-button"
            @click="emit('reference', 'field', field.candidate_id, field.label)"
          >
            引用到对话
          </button>
        </article>
      </section>
      <p
        v-if="!groups.size"
        class="muted"
      >
        没有匹配的字段。
      </p>
    </template>
    <div
      v-else
      class="capture-images"
    >
      <p
        v-if="!capture.snapshot.samples.length"
        class="muted"
      >
        未发现图片来源，不代表业务不要求材料。
      </p>
      <figure
        v-for="(sample, index) in capture.snapshot.samples"
        :key="sample.sample_id"
      >
        <button
          class="image-preview"
          :aria-label="`查看${sample.label || '未确认材料'}`"
          @click="viewer = index"
        >
          <img
            v-if="sample.url && !failed.has(sample.sample_id)"
            :src="sample.url"
            :alt="sample.label || '未确认材料名称'"
            loading="lazy"
            referrerpolicy="no-referrer"
            @error="fail(sample.sample_id)"
          />
          <span v-else>{{
            sample.url ? "来源暂不可用，点击查看说明" : "未取得 HTTP(S) 原始地址"
          }}</span>
        </button>
        <figcaption>
          <strong>{{ sample.label || "材料名称未确认" }}</strong
          ><small>{{ sample.section || sample.group || "分区未确认" }}</small>
        </figcaption>
        <button
          v-if="quotable"
          class="text-button"
          @click="emit('reference', 'sample', sample.sample_id, sample.label || '未确认材料')"
        >
          引用到对话
        </button>
      </figure>
    </div>
    <ImageViewer
      v-if="viewer !== null"
      :samples="capture.snapshot.samples"
      :initial="viewer"
      @close="viewer = null"
    />
  </section>
</template>

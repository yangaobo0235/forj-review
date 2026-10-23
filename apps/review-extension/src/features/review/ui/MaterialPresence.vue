<script setup lang="ts">
import { ref, watch } from "vue";
import type { MaterialPresenceReport } from "../../../shared/contracts/review";

const props = defineProps<{
  report: MaterialPresenceReport;
  onFocusImage?: (imageId: string) => void | Promise<{ ok: boolean; error?: string }>;
}>();
const expanded = ref(props.report.status !== "COMPLETE");
watch(
  () => props.report.status,
  (status) => {
    expanded.value = status !== "COMPLETE";
  },
);
const labels = { PRESENT: "已上传", MISSING: "缺少", UNCERTAIN: "待确认" };
const summaries = { COMPLETE: "材料齐全", INCOMPLETE: "材料有缺项", UNCERTAIN: "部分材料待确认" };
</script>

<template>
  <details
    class="material-presence"
    :open="expanded"
    @toggle="expanded = ($event.target as HTMLDetailsElement).open"
  >
    <summary>
      <strong>材料完整性</strong>
      <span :class="['presence-summary', report.status.toLowerCase()]">{{
        summaries[report.status]
      }}</span>
      <span class="presence-toggle">{{ expanded ? "收起" : "展开" }}</span>
    </summary>
    <div class="presence-body">
      <p class="presence-caption">根据材料识别结果核对完整性，具体字段在下方逐项核对。</p>
      <p
        v-for="notice in report.notices"
        :key="notice"
        class="presence-notice"
      >
        {{ notice }}
      </p>
      <section
        v-for="group in report.groups"
        :key="group.key"
        class="presence-group"
      >
        <div class="presence-group-heading">
          <h3>{{ group.display_name }}</h3>
          <span :class="['presence-status', group.status.toLowerCase()]">{{
            group.status === "PRESENT" ? "已满足" : labels[group.status]
          }}</span>
        </div>
        <p
          v-if="group.alternatives.length > 1"
          class="presence-caption"
        >
          满足任一方案即可，也可同时提供。
        </p>
        <template
          v-for="(option, index) in group.alternatives"
          :key="index"
        >
          <p
            v-if="index"
            class="presence-or"
          >
            或
          </p>
          <ul>
            <li
              v-for="item in option"
              :key="item.key"
            >
              <div class="presence-item">
                <span>{{ item.display_name }}</span
                ><span
                  :class="[
                    'presence-status',
                    group.alternatives.length > 1 &&
                    group.status === 'PRESENT' &&
                    item.status === 'MISSING'
                      ? 'optional'
                      : item.status.toLowerCase(),
                  ]"
                  >{{
                    group.alternatives.length > 1 &&
                    group.status === "PRESENT" &&
                    item.status === "MISSING"
                      ? "未提供（可选）"
                      : labels[item.status]
                  }}</span
                >
              </div>
              <div
                v-if="item.image_ids.length && onFocusImage"
                class="presence-images"
              >
                <button
                  v-for="(imageId, imageIndex) in item.image_ids"
                  :key="imageId"
                  type="button"
                  :aria-label="`${item.display_name}，${item.status === 'UNCERTAIN' ? '待确认图片' : '原图'} ${imageIndex + 1}`"
                  @click="onFocusImage?.(imageId)"
                >
                  {{ item.status === "UNCERTAIN" ? "查看待确认图片" : "查看原图"
                  }}{{ item.image_ids.length > 1 ? ` ${imageIndex + 1}` : "" }}
                </button>
              </div>
              <p
                v-if="item.status === 'UNCERTAIN'"
                class="presence-reason"
              >
                {{ item.reason }}
              </p>
            </li>
          </ul>
        </template>
      </section>
    </div>
  </details>
</template>

<style scoped>
.material-presence {
  border: 0;
  padding: 0;
  background: var(--review-surface);
  margin: 0;
  font-size: var(--review-font-meta);
}
.review-progress + .material-presence {
  border-top: 1px solid var(--review-border);
  margin-top: 12px;
  padding-top: 12px;
}
summary {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  list-style: none;
}
summary::-webkit-details-marker {
  display: none;
}
summary strong {
  color: var(--review-secondary);
  font-size: var(--review-font-meta);
  font-weight: 500;
}
.presence-toggle {
  margin-left: auto;
  color: var(--review-muted);
  font-size: var(--review-font-caption);
}
.presence-body {
  padding-top: 10px;
}
.presence-caption,
.presence-reason {
  color: var(--review-muted);
  font-size: var(--review-font-caption);
  line-height: 1.6;
  margin: 0 0 8px;
}
.presence-notice {
  color: var(--review-warning);
  background: #fff7e8;
  padding: 8px;
  border-radius: 6px;
  overflow-wrap: anywhere;
}
.presence-group {
  border-top: 1px solid var(--review-border);
  padding-top: 10px;
  margin-top: 10px;
}
.presence-group-heading,
.presence-item {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}
.presence-item > span:first-child {
  min-width: 0;
  overflow-wrap: anywhere;
}
h3 {
  color: var(--review-text);
  font-size: var(--review-font-meta);
  font-weight: 500;
  margin: 0;
}
ul {
  padding: 0;
  margin: 6px 0 0;
  list-style: none;
}
li {
  padding: 6px 0;
}
.presence-status,
.presence-summary {
  font-size: var(--review-font-caption);
  white-space: nowrap;
}
.present,
.complete {
  color: var(--review-success);
}
.missing,
.incomplete {
  color: var(--review-danger);
}
.uncertain {
  color: var(--review-warning);
}
.optional,
.presence-or {
  color: var(--review-muted);
}
.presence-or {
  margin: 6px 0 0;
  font-size: var(--review-font-caption);
}
.presence-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}
.presence-images button {
  min-height: 28px;
  padding: 3px 0;
  border: 0;
  background: transparent;
  color: var(--review-primary);
  font-size: var(--review-font-meta);
  cursor: pointer;
}
.presence-images button:hover {
  text-decoration: underline;
}
.presence-reason {
  margin: 4px 0 0;
}
</style>

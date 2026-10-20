<script setup lang="ts">
import { ref, watch } from "vue";
import type { MaterialPresenceReport } from "../../../shared/contracts/review";

const props = defineProps<{
  report: MaterialPresenceReport;
  onFocusImage?: (imageId: string) => void | Promise<{ ok: boolean; error?: string }>;
}>();
const expanded = ref(props.report.status !== "COMPLETE");
watch(() => props.report.status, status => { expanded.value = status !== "COMPLETE"; });
const labels = { PRESENT: "已上传", MISSING: "缺少", UNCERTAIN: "待确认" };
const summaries = { COMPLETE: "材料齐全", INCOMPLETE: "材料有缺项", UNCERTAIN: "部分材料待确认" };
</script>

<template>
  <details class="material-presence" :open="expanded" @toggle="expanded = ($event.target as HTMLDetailsElement).open">
    <summary>
      <strong>材料完整性</strong>
      <span :class="['presence-summary', report.status.toLowerCase()]">{{ summaries[report.status] }}</span>
      <span class="presence-toggle">{{ expanded ? '收起' : '展开' }}</span>
    </summary>
    <div class="presence-body">
      <p class="presence-caption">仅检查材料是否上传，字段识别结果在下方核对。</p>
      <p v-for="notice in report.notices" :key="notice" class="presence-notice">{{ notice }}</p>
      <section v-for="group in report.groups" :key="group.key" class="presence-group">
        <div class="presence-group-heading"><h3>{{ group.display_name }}</h3><span :class="['presence-status', group.status.toLowerCase()]">{{ group.status === 'PRESENT' ? '已满足' : labels[group.status] }}</span></div>
        <p v-if="group.alternatives.length > 1" class="presence-caption">满足任一方案即可，也可同时提供。</p>
        <template v-for="(option, index) in group.alternatives" :key="index">
          <p v-if="index" class="presence-or">或</p>
          <ul>
            <li v-for="item in option" :key="item.key">
              <div class="presence-item"><span>{{ item.display_name }}</span><span :class="['presence-status', group.alternatives.length > 1 && group.status === 'PRESENT' && item.status === 'MISSING' ? 'optional' : item.status.toLowerCase()]">{{ group.alternatives.length > 1 && group.status === 'PRESENT' && item.status === 'MISSING' ? '未提供（可选）' : labels[item.status] }}</span></div>
              <div v-if="item.image_ids.length && onFocusImage" class="presence-images">
                <button v-for="(imageId, imageIndex) in item.image_ids" :key="imageId" type="button"
                  :aria-label="`${item.display_name}，${item.status === 'UNCERTAIN' ? '待确认图片' : '原图'} ${imageIndex + 1}`"
                  @click="onFocusImage?.(imageId)">{{ item.status === 'UNCERTAIN' ? '查看待确认图片' : '查看原图' }}{{ item.image_ids.length > 1 ? ` ${imageIndex + 1}` : '' }}</button>
              </div>
              <p v-if="item.status === 'UNCERTAIN'" class="presence-reason">{{ item.reason }}</p>
            </li>
          </ul>
        </template>
      </section>
    </div>
  </details>
</template>

<style scoped>
.material-presence { border: 1px solid #dbe3e8; border-radius: 10px; background: #fff; margin: 12px 0; font-size: 12px; }
summary { display: flex; align-items: center; gap: 10px; padding: 12px; cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
summary strong { color: #263442; font-size: 13px; }
.presence-toggle { margin-left: auto; color: #64748b; font-size: 11px; }
.presence-body { padding: 0 12px 12px; }
.presence-caption, .presence-reason { color: #64748b; font-size: 11px; line-height: 1.6; margin: 0 0 8px; }
.presence-notice { color: #8a570c; background: #fff8e8; padding: 8px; border-radius: 5px; }
.presence-group { border-top: 1px solid #edf0f3; padding-top: 10px; margin-top: 10px; }
.presence-group-heading, .presence-item { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
h3 { color: #334155; font-size: 12px; font-weight: 600; margin: 0; }
ul { padding: 0; margin: 6px 0 0; list-style: none; }
li { padding: 6px 0; }
.presence-status, .presence-summary { font-size: 11px; white-space: nowrap; }
.present, .complete { color: #13735c; }
.missing, .incomplete { color: #b04436; }
.uncertain { color: #95610d; }
.optional { color: #64748b; }
.presence-or { margin: 6px 0 0; color: #64748b; font-size: 11px; }
.presence-images { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
.presence-images button { padding: 0; border: 0; background: transparent; color: #276a98; font-size: 11px; cursor: pointer; }
.presence-images button:hover { text-decoration: underline; }
.presence-reason { margin: 4px 0 0; }
</style>

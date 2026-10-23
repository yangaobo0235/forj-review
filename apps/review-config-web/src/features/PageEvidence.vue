<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from "vue";
import type { CapturedPage } from "@forj/configuration-contracts";
import type { Workbench } from "../model/use-workbench";
import { request } from "../api/client";
import { displayTime } from "../model/presentation";
import CaptureEvidence from "./CaptureEvidence.vue";
const { work } = defineProps<{ work: Workbench }>();
const emit = defineEmits<{ chat: [] }>();
const selected = ref<CapturedPage | null>(null),
  loading = ref(false),
  error = ref("");
const controller = new AbortController();
onScopeDispose(() => controller.abort());
let sequence = 0;
watch(
  () => work.source,
  (value) => {
    if (!selected.value || value?.capture_id === work.session?.capture_id) selected.value = value;
  },
  { immediate: true },
);
const choices = computed(() =>
  work.captures.filter((c) => !c.page_id || c.page_id === work.session?.draft.page_id),
);
const current = computed(() => selected.value?.capture_id === work.session?.capture_id);
async function browse(id: string) {
  const token = ++sequence;
  loading.value = true;
  error.value = "";
  try {
    const value = await request<CapturedPage>(
      `/captures/${encodeURIComponent(id)}`,
      undefined,
      undefined,
      controller.signal,
    );
    if (token === sequence) selected.value = value;
  } catch (e) {
    if (!controller.signal.aborted && token === sequence)
      error.value = e instanceof Error ? e.message : "读取失败";
  } finally {
    if (token === sequence) loading.value = false;
  }
}
function quote(kind: "field" | "sample", id: string, label: string) {
  work.quote(kind, id, label);
  emit("chat");
}
</script>
<template>
  <section aria-label="浏览页面资料">
    <div class="panel-heading">
      <h3>页面资料</h3>
      <button
        class="text-button"
        :disabled="Boolean(work.busy)"
        @click="work.run('读取采集目录', work.openScope)"
      >
        关联其他采集
      </button>
    </div>
    <p class="muted small">
      查看历史资料不会更换任务现场。当前修改基于正式活动配置 {{ work.session?.baseline_version }}。
    </p>
    <label
      >查看采集<select
        :value="selected?.capture_id || ''"
        :disabled="loading"
        @change="browse(($event.target as HTMLSelectElement).value)"
      >
        <option
          v-if="work.source && !choices.some((c) => c.capture_id === work.source!.capture_id)"
          :value="work.source.capture_id"
        >
          当前现场 · {{ displayTime(work.source.created_at) }}
        </option>
        <option
          v-for="capture in choices"
          :key="capture.capture_id"
          :value="capture.capture_id"
        >
          {{ displayTime(capture.created_at) }} · {{ capture.title
          }}{{ capture.capture_id === work.session?.capture_id ? " · 当前现场" : "" }}
        </option>
      </select></label
    >
    <p
      v-if="loading"
      role="status"
    >
      正在读取资料…
    </p>
    <p
      v-if="error"
      role="alert"
      class="inline-error"
    >
      {{ error }}
    </p>
    <template v-if="selected">
      <div
        v-if="!current"
        class="surface"
      >
        <p>正在查看历史采集。关联后保留目标和草稿，相关证据需要重新验证。</p>
        <button
          class="secondary"
          :disabled="
            Boolean(work.busy) || work.running || Boolean(work.session?.pending?.confirmed)
          "
          @click="work.run('关联为当前现场', () => work.attachCapture(selected))"
        >
          关联为当前现场
        </button>
      </div>
      <CaptureEvidence
        :key="selected.capture_id"
        :capture="selected"
        :quotable="current && !work.session?.receipt && work.session?.state !== 'CLOSED'"
        @reference="quote"
      />
    </template>
    <p
      v-else
      class="muted"
    >
      请选择采集查看。原浏览器关闭后仍可调查；实际页面检查需要重新关联可访问的现场。
    </p>
  </section>
</template>

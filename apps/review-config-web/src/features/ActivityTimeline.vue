<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { Workbench } from "../model/use-workbench";
import { activityItems, activityStatus, type ActivityItem } from "../model/activity";
import TaskRecovery from "./TaskRecovery.vue";

const { work } = defineProps<{ work: Workbench }>();
const emit = defineEmits<{ evidence: [] }>();
const items = computed(() => activityItems(work.events));
const latest = computed(() => items.value.at(-1));
const scroll = ref<HTMLElement | null>(null);
const following = ref(true),
  unread = ref(0),
  now = ref(Date.now()),
  copyNotice = ref("");
const positionKey = `review.workspace.v3.activity.${work.sessionId}`;
let timer: ReturnType<typeof setInterval> | undefined;
let loadingHistory = false;
function onScroll() {
  const element = scroll.value;
  if (!element || loadingHistory) return;
  following.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
  if (following.value) unread.value = 0;
}
async function bottom() {
  following.value = true;
  unread.value = 0;
  await nextTick();
  if (scroll.value) scroll.value.scrollTop = scroll.value.scrollHeight;
}
watch(
  () => JSON.stringify(items.value),
  async (_value, old) => {
    await nextTick();
    if (loadingHistory) return;
    if (following.value) await bottom();
    else if (old !== undefined) unread.value++;
  },
  { flush: "post" },
);
async function earlier() {
  const element = scroll.value;
  if (!element) return;
  const height = element.scrollHeight,
    top = element.scrollTop;
  loadingHistory = true;
  await work.run("读取更早处理记录", work.loadEarlierEvents);
  await nextTick();
  element.scrollTop = top + element.scrollHeight - height;
  following.value = false;
  loadingHistory = false;
}
function expand(event: Event) {
  if ((event.target as HTMLDetailsElement).open) following.value = false;
}
function duration(item: ActivityItem) {
  const seconds =
    item.durationMs !== undefined
      ? item.durationMs / 1000
      : item.status === "RUNNING" && item.startedAt
        ? (now.value - Date.parse(item.startedAt)) / 1000
        : undefined;
  return seconds !== undefined && Number.isFinite(seconds)
    ? `${Math.max(0, Math.floor(seconds))} 秒`
    : "";
}
async function copyDiagnostic() {
  try {
    await navigator.clipboard.writeText(
      `task_id=${work.sessionId}\nturn_id=${latest.value?.runId ?? ""}`,
    );
    copyNotice.value = "诊断编号已复制";
  } catch {
    copyNotice.value = `任务编号：${work.sessionId}`;
  }
}
onMounted(async () => {
  await nextTick();
  const saved = sessionStorage.getItem(positionKey);
  if (saved && scroll.value) {
    try {
      const value = JSON.parse(saved);
      following.value = value.following === true;
      if (!following.value) scroll.value.scrollTop = Number(value.top) || 0;
    } catch {
      following.value = true;
    }
  }
  if (following.value) await bottom();
  timer = setInterval(() => {
    now.value = Date.now();
  }, 1000);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  sessionStorage.setItem(
    positionKey,
    JSON.stringify({ top: scroll.value?.scrollTop ?? 0, following: following.value }),
  );
});
</script>

<template>
  <section
    class="activity-feed"
    aria-label="任务处理记录"
  >
    <header
      class="activity-current"
      role="status"
    >
      <div>
        <strong>{{ latest?.title ?? "等待处理" }}</strong
        ><span
          v-if="latest"
          class="tag"
          >{{ activityStatus[latest.status] ?? latest.status }} {{ duration(latest) }}</span
        >
      </div>
      <p>{{ latest?.detail ?? "开始处理后，这里会显示实际执行动作与发现。" }}</p>
      <button
        class="text-button"
        @click="copyDiagnostic"
      >
        复制诊断编号</button
      ><small>{{ copyNotice }}</small>
    </header>
    <div
      ref="scroll"
      class="activity-scroll"
      @scroll="onScroll"
    >
      <button
        v-if="work.hasEarlierEvents"
        class="text-button"
        :disabled="work.loadingEarlierEvents"
        @click="earlier"
      >
        {{ work.loadingEarlierEvents ? "正在读取…" : "加载更早记录" }}
      </button>
      <ol class="activity-timeline">
        <li
          v-for="(item, index) in items"
          :key="item.id"
          :data-status="item.status"
        >
          <p
            v-if="index === 0 || item.runId !== items[index - 1]?.runId"
            class="activity-round"
          >
            处理轮次 · {{ item.runId.slice(0, 8) }}
          </p>
          <div class="activity-title">
            <strong>{{ item.title }}</strong
            ><small
              >{{ activityStatus[item.status] ?? item.status }} · {{ duration(item) || "—" }}</small
            >
          </div>
          <p>{{ item.detail }}</p>
          <p
            v-if="item.nextAction"
            class="muted small"
          >
            {{ item.nextAction }}
          </p>
          <button
            v-if="
              ['analyze_materials', 'inspect_material_region', 'get_page_snapshot'].includes(
                item.tool ?? '',
              )
            "
            class="text-button"
            @click="emit('evidence')"
          >
            查看页面资料
          </button>
          <details @toggle="expand">
            <summary>排障信息</summary>
            <dl class="activity-diagnostic">
              <dt>任务</dt>
              <dd>{{ work.sessionId }}</dd>
              <dt>轮次</dt>
              <dd>{{ item.runId }}</dd>
              <template v-if="item.callId"
                ><dt>调用</dt>
                <dd>{{ item.callId }}</dd></template
              ><template v-if="item.tool"
                ><dt>操作</dt>
                <dd>{{ item.tool }}</dd></template
              ><template v-if="item.code"
                ><dt>错误码</dt>
                <dd>{{ item.code }}</dd></template
              >
              <dt>开始时间</dt>
              <dd>
                {{
                  item.startedAt ? new Date(item.startedAt).toLocaleString("zh-CN") : "旧记录未提供"
                }}
              </dd>
            </dl>
          </details>
        </li>
      </ol>
      <p
        v-if="!items.length"
        class="muted"
      >
        尚无处理记录。
      </p>
      <TaskRecovery :work="work" />
    </div>
    <button
      v-if="!following"
      class="activity-latest"
      @click="bottom"
    >
      {{ unread ? `${unread} 次新进展 · ` : "" }}返回最新
    </button>
  </section>
</template>

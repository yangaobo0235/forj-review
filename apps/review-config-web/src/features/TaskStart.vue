<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Workbench } from "../model/use-workbench";
import CapturePicker from "./CapturePicker.vue";
import Icon from "../ui/Icon.vue";
const { work } = defineProps<{ work: Workbench }>();
const pageId = ref(""),
  rollbackVersion = ref("");
const message = computed({
  get: () => work.messageDraft,
  set: (value: string) => work.setMessageDraft(value),
});
const selected = computed(() => work.templates.find((p) => p.page_id === pageId.value));
watch(
  () => work.capture?.capture_id,
  () => {
    const id = work.capture?.snapshot.detected_page_id;
    pageId.value = id && work.templates.some((p) => p.page_id === id) ? id : "";
  },
  { immediate: true },
);
watch(pageId, () => {
  rollbackVersion.value = "";
});
const examples = [
  "把开票时间的显示名称改成开票日期，日期范围保持不变。",
  "发动机号采错了位置，帮我调查并修正。",
  "梳理当前材料要求，存在歧义时先问我。",
];
function start() {
  if (
    work.busy ||
    !work.capture ||
    !pageId.value ||
    (!message.value.trim() && !rollbackVersion.value)
  )
    return;
  void work.run("创建修改任务", () =>
    work.startTask({
      pageId: pageId.value,
      message: message.value,
      rollbackVersion: rollbackVersion.value,
    }),
  );
}
</script>

<template>
  <section
    class="task-start"
    aria-labelledby="start-title"
  >
    <header class="start-heading">
      <span class="eyebrow">已有审核页面修改助手</span>
      <h1 id="start-title">
        {{ work.capture ? "页面资料已就绪，开始描述修改" : "准备好页面资料，开始一次修改" }}
      </h1>
      <p class="muted">先关联页面采集，再用自然语言描述要求。助手调查并验证，你审阅后确认发布。</p>
    </header>
    <form
      class="start-grid"
      @submit.prevent="start"
    >
      <section
        class="start-panel"
        aria-labelledby="source-title"
      >
        <header class="start-panel-heading">
          <span class="start-step"
            ><Icon
              name="pageFields"
              :size="21"
          /></span>
          <div>
            <h2 id="source-title">1. 准备页面资料</h2>
            <p>新采集或选择历史采集，确认现场</p>
          </div>
          <span class="tag">{{ work.capture ? "已关联" : "待关联" }}</span>
        </header>
        <div class="start-panel-scroll">
          <CapturePicker :work="work" />
        </div>
        <footer class="start-panel-note">
          <Icon
            name="shield"
            :size="16"
          /><span>保留字段原值与图片地址，无需逐项勾选；助手按需查阅。</span>
        </footer>
      </section>
      <section
        class="start-panel"
        aria-labelledby="intent-title"
      >
        <header class="start-panel-heading">
          <span class="start-step"
            ><Icon
              name="chat"
              :size="21"
          /></span>
          <div>
            <h2 id="intent-title">2. 描述修改要求</h2>
            <p>修改已有页面，基于当前正式配置</p>
          </div>
        </header>
        <div class="start-panel-scroll start-intent">
          <label
            >已有审核页面<select
              v-model="pageId"
              aria-label="已有审核页面"
              :disabled="
                Boolean(work.busy) ||
                !work.capture ||
                Boolean(work.capture?.snapshot.detected_page_id === pageId && selected)
              "
            >
              <option
                value=""
                disabled
              >
                选择需要修改的页面
              </option>
              <option
                v-for="page in work.templates"
                :key="page.page_id"
                :value="page.page_id"
              >
                {{ page.title }} · {{ page.region }}
              </option>
            </select></label
          >
          <label
            >修改要求<textarea
              v-model="message"
              rows="4"
              aria-label="修改要求"
              placeholder="描述希望改变的行为；无需填写字段编码或选择所有图片。"
              :disabled="Boolean(work.busy) || !work.capture"
            />
          </label>
          <p class="small muted example-heading">可以这样描述</p>
          <div class="example-list">
            <button
              v-for="example in examples"
              :key="example"
              type="button"
              class="secondary"
              :disabled="Boolean(work.busy) || !work.capture"
              @click="message = example"
            >
              {{ example }}
            </button>
          </div>
          <details v-if="selected?.versions.length">
            <summary>从历史版本恢复</summary>
            <label
              >历史版本<select v-model="rollbackVersion">
                <option value="">创建新版本并修改</option>
                <option
                  v-for="version in selected.versions"
                  :key="version"
                  :value="version"
                >
                  恢复 {{ version }}
                </option>
              </select></label
            >
            <p class="muted small">历史恢复同样需要验证、明确发布与采用核对。</p>
          </details>
        </div>
        <footer class="start-submit">
          <div class="action-row">
            <span class="muted small">{{
              work.capture ? "现场资料已保存。最终发布需要你明确确认。" : "请先关联页面采集。"
            }}</span
            ><button
              class="primary"
              :disabled="
                Boolean(work.busy) ||
                !work.capture ||
                !pageId ||
                (!message.trim() && !rollbackVersion)
              "
            >
              {{ work.busy || "开始修改" }}
            </button>
          </div>
        </footer>
      </section>
    </form>
  </section>
</template>

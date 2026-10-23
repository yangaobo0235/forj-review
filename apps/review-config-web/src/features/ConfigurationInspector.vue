<script setup lang="ts">
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import type { Workbench } from "../model/use-workbench";
import { request } from "../api/client";
import ChangePreview from "./ChangePreview.vue";
import AcceptanceCases from "./AcceptanceCases.vue";
import FieldEditor from "./FieldEditor.vue";
import PageOptionsEditor from "./PageOptionsEditor.vue";
import ModalDialog from "../ui/ModalDialog.vue";
import ActivityTimeline from "./ActivityTimeline.vue";
import PageEvidence from "./PageEvidence.vue";
import { activityItems } from "../model/activity";

const { work } = defineProps<{ work: Workbench }>();
const emit = defineEmits<{ message: [text: string]; chat: [] }>();
const session = computed(() => work.session!);
const tab = defineModel<string>("tab", { default: "result" });
const expert = ref(false),
  scroll = ref<HTMLElement | null>(null);
const currentActivity = computed(() => activityItems(work.events).at(-1));
const positionKey = (pane: string) => `review.workspace.v2.scroll.${work.sessionId}.${pane}`;
function savePosition(pane: string) {
  if (scroll.value) sessionStorage.setItem(positionKey(pane), String(scroll.value.scrollTop));
}
async function restorePosition() {
  await nextTick();
  if (scroll.value)
    scroll.value.scrollTop = Number(sessionStorage.getItem(positionKey(tab.value)) || 0);
}
watch(tab, (_next, previous) => {
  savePosition(previous);
  void restorePosition();
});
onMounted(restorePosition);
onBeforeUnmount(() => savePosition(tab.value));
const confirmation = ref<{
  id: string;
  revision: number;
  intent: number | undefined;
  evidence: number | undefined;
} | null>(null);
const busy = computed(() => Boolean(work.busy) || work.running);
const diff = computed(() => session.value.report?.diff ?? session.value.preview?.diff);
const requirements = computed(() => session.value.task.coverage);
watch(
  () => [
    session.value.revision,
    session.value.intent_revision,
    session.value.evidence_revision,
    session.value.report?.report_id,
    session.value.receipt,
    session.value.task.can_publish,
  ],
  () => {
    if (
      confirmation.value &&
      (confirmation.value.id !== session.value.report?.report_id ||
        confirmation.value.revision !== session.value.revision ||
        confirmation.value.intent !== session.value.intent_revision ||
        confirmation.value.evidence !== session.value.evidence_revision ||
        session.value.receipt ||
        session.value.task.can_publish === false)
    )
      confirmation.value = null;
  },
);
function openConfirmation() {
  if (session.value.report)
    confirmation.value = {
      id: session.value.report.report_id,
      revision: session.value.revision,
      intent: session.value.intent_revision,
      evidence: session.value.evidence_revision,
    };
}
async function handoff() {
  await work.run("导出开发接续任务", async () => {
    const sid = session.value.task_id;
    const value = await request(`/tasks/${sid}/handoff`);
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `review-task-${sid}.json`;
    link.click();
    URL.revokeObjectURL(url);
    work.setNotice("已导出接续任务，由开发人员本地显式启动；导出不表示需求已实现。");
  });
}
function followup() {
  const pageId = String(session.value.draft.page_id),
    sid = session.value.task_id;
  void work.run("创建后续任务", () =>
    work.startTask({ pageId, message: "", parentSessionId: sid }, work.source),
  );
}
</script>

<template>
  <section
    class="inspector"
    aria-label="修改结果"
  >
    <header class="inspector-heading">
      <h2>{{ session.task.label || "当前修改结果" }}</h2>
      <p
        v-if="work.running && currentActivity"
        class="muted small"
      >
        {{ currentActivity.title }}
      </p>
    </header>
    <nav
      class="workspace-tabs"
      aria-label="工作区内容"
    >
      <button
        :aria-pressed="tab === 'result'"
        @click="tab = 'result'"
      >
        修改结果
      </button>
      <button
        :aria-pressed="tab === 'evidence'"
        @click="tab = 'evidence'"
      >
        页面资料
      </button>
      <button
        :aria-pressed="tab === 'history'"
        @click="tab = 'history'"
      >
        处理记录
      </button>
    </nav>
    <div
      v-show="tab !== 'history'"
      ref="scroll"
      class="inspector-scroll"
    >
      <div
        v-show="tab === 'result'"
        class="result-content"
      >
        <section
          class="requirements"
          aria-label="当前有效修改要求"
        >
          <span class="eyebrow">当前修改要求</span>
          <div
            v-for="item in requirements"
            :key="item.requirement_id"
            class="requirement-row"
          >
            <div>
              <p>{{ item.text }}</p>
              <small
                v-for="a in item.acceptance"
                :key="a"
                class="muted"
                >{{ a }}</small
              >
            </div>
            <span class="tag">{{
              item.verified ? "验证已覆盖" : item.resolved ? "已处理，待验证" : "待处理"
            }}</span
            ><button
              v-if="!session.receipt && session.state !== 'CLOSED'"
              class="text-button"
              @click="emit('message', `关于「${item.text}」，我想更正：`)"
            >
              改述
            </button>
          </div>
          <p
            v-if="!requirements.length"
            class="muted"
          >
            还没有有效修改目标，可以先在对话中描述。
          </p>
        </section>
        <div
          v-if="session.state === 'CLOSED'"
          class="surface"
        >
          <h3>任务已归档</h3>
          <p>需求与候选已保留。恢复后重新核对当前配置与现场。</p>
          <button
            class="primary"
            :disabled="busy"
            @click="work.run('恢复任务', () => work.action('restore'))"
          >
            恢复任务
          </button>
        </div>
        <div
          v-else-if="session.route === 'LOCAL_CODE' || session.route === 'HUMAN_HANDOFF'"
          class="surface"
        >
          <h3>
            {{ session.route === "LOCAL_CODE" ? "这项需求需要补充代码能力" : "需要人工设计决定" }}
          </h3>
          <p>{{ session.route_assessment?.reason }}</p>
          <p
            v-for="(gap, i) in session.capability_gaps"
            :key="i"
          >
            {{ gap.missing_capability }}
          </p>
          <p class="muted">
            {{
              session.route === "LOCAL_CODE"
                ? "导出后，由开发人员显式运行 forj-review-config-develop，在独立副本调查、修改和检查，交付补丁与未验证项。本机网页仍是配置模式，不会直接修改源码。"
                : "人工接续材料须包含实际调查、可靠实现或验收障碍，以及具体决策事项；需求尚未实现。"
            }}
          </p>
          <button
            v-if="session.route === 'LOCAL_CODE'"
            class="primary"
            :disabled="busy"
            @click="handoff"
          >
            导出开发接续任务
          </button>
        </div>
        <div
          v-else-if="session.waiting_reason === 'CAPTURE' && !session.receipt"
          class="surface"
        >
          <h3>准备结果已保留，需要业务页面现场</h3>
          <p>核对实际控件、页面身份和采集行为；无需全量选择字段或图片。</p>
          <button
            class="primary"
            :disabled="busy"
            @click="work.run('读取页面现场', work.openScope)"
          >
            关联现场并继续
          </button>
        </div>
        <div
          v-if="session.pending?.kind === 'question'"
          class="surface"
        >
          <h3>有一个关键问题需要回答</h3>
          <p>{{ session.pending.question }}</p>
          <button
            class="secondary"
            @click="emit('chat')"
          >
            到对话中回答或自然语言说明
          </button>
        </div>
        <ChangePreview :diff="diff" />
        <section
          v-if="session.report"
          class="validation-section"
          aria-label="验证结果"
        >
          <div class="panel-heading">
            <h3>验证与需求覆盖</h3>
            <span class="tag">{{ session.report.ok ? "必要检查通过" : "尚有待处理项" }}</span>
          </div>
          <div
            v-for="check in session.report.checks"
            :key="check.name"
            class="result-row"
          >
            <span>{{ check.name }}</span
            ><strong :class="{ 'success-text': check.ok }">{{
              check.ok ? "通过" : "待完成"
            }}</strong>
          </div>
          <p
            v-for="blocker in session.report.blockers"
            :key="blocker"
            class="blocker"
          >
            {{ blocker }}
          </p>
          <details>
            <summary>查看验证依据与范围</summary>
            <p
              v-for="item in session.report.decision_summary"
              :key="item"
            >
              {{ item }}
            </p>
            <p class="muted small">
              当前候选修订
              {{
                session.revision
              }}；静态检查、真实页面采集、独立样例与需求覆盖分别核对。发布后还需采用核对。
            </p>
          </details>
        </section>
        <p
          v-else
          class="muted small"
        >
          Agent会自动组织相关验证；没有验证报告不表示已经通过。
        </p>
        <details
          v-if="!session.receipt && session.state !== 'CLOSED'"
          class="acceptance-section"
        >
          <summary>业务验收预期：仅缺少独立口径时补充</summary>
          <AcceptanceCases :work="work" />
        </details>
        <section
          v-if="session.receipt"
          class="activation-section"
          aria-label="发布与采用核对"
        >
          <h3>
            {{ session.task.complete ? "配置已生效，本次任务完成" : "已发布，继续核对实际采用" }}
          </h3>
          <div class="result-row">
            <span>配置发布记录</span><strong class="success-text">已保存</strong>
          </div>
          <div class="result-row">
            <span>目标审核服务加载</span
            ><strong>{{
              session.activation?.state === "EFFECTIVE"
                ? "已核对"
                : session.activation?.reason || "等待核对"
            }}</strong>
          </div>
          <div class="result-row">
            <span>指定浏览器使用本次配置采集</span
            ><strong>{{ session.adoption ? "已核对" : "等待核对" }}</strong>
          </div>
          <p class="muted small">
            目标版本
            {{
              session.receipt.version
            }}。只覆盖实际核对的服务与浏览器；已开始的审核任务保留原快照。
          </p>
          <div class="action-row">
            <button
              class="secondary"
              :disabled="Boolean(work.busy)"
              @click="work.run('核对服务加载', () => work.action('activation'))"
            >
              重新核对服务加载</button
            ><button
              v-if="!session.adoption"
              class="primary"
              :disabled="Boolean(work.busy) || session.activation?.state !== 'EFFECTIVE'"
              @click="work.run('核对浏览器实际使用', work.adoption)"
            >
              核对业务浏览器使用</button
            ><button
              v-if="!session.adoption"
              class="secondary"
              :disabled="Boolean(work.busy)"
              @click="work.run('重新关联现场', work.openScope)"
            >
              页面已刷新，重新关联
            </button>
          </div>
        </section>
        <details
          class="expert-section"
          @toggle="expert = ($event.target as HTMLDetailsElement).open"
        >
          <summary>专家维护与技术详情</summary>
          <template v-if="expert"
            ><p class="muted small">与Agent共用一个候选。人工修改使旧验证与确认失效。</p>
            <FieldEditor :work="work" />
            <details>
              <summary>页面高级选项</summary>
              <PageOptionsEditor :work="work" />
            </details>
            <details>
              <summary>完整配置差异</summary>
              <pre>{{ JSON.stringify(diff, null, 2) }}</pre>
            </details></template
          >
        </details>
      </div>
      <PageEvidence
        v-show="tab === 'evidence'"
        :work="work"
        @chat="emit('chat')"
      />
    </div>
    <ActivityTimeline
      v-if="tab === 'history'"
      :work="work"
      @evidence="tab = 'evidence'"
    />
    <footer
      v-if="tab === 'result'"
      class="task-actions"
    >
      <p class="muted small">
        {{
          session.receipt
            ? "本次发布记录保持只读，后续修改从最新活动配置开始。"
            : "尚未发布。修改经验证、审阅并明确确认后才进入正式配置。"
        }}
      </p>
      <div class="action-row">
        <button
          v-if="session.task.can_publish"
          class="primary"
          :disabled="busy"
          @click="openConfirmation"
        >
          审阅并发布</button
        ><button
          v-if="
            !session.receipt && !session.pending?.confirmed && !busy && session.state !== 'CLOSED'
          "
          class="secondary"
          @click="work.run('继续处理', () => work.action('resume'))"
        >
          {{
            ["FAILED", "INTERRUPTED", "CANCELLED"].includes(session.turn_status)
              ? "核对并继续处理"
              : "继续调查与验证"
          }}</button
        ><button
          v-if="session.pending?.confirmed"
          class="primary"
          :disabled="busy"
          @click="work.run('核对原发布结果', () => work.action('resume'))"
        >
          核对原发布结果</button
        ><button
          v-if="session.receipt"
          class="primary"
          :disabled="Boolean(work.busy)"
          @click="followup"
        >
          基于此页继续修改</button
        ><button
          v-if="!session.receipt && session.state !== 'CLOSED'"
          class="text-button"
          :disabled="busy || Boolean(session.pending?.confirmed)"
          @click="work.run('归档任务', () => work.action('archive'))"
        >
          归档任务
        </button>
      </div>
    </footer>
    <ModalDialog
      v-if="confirmation"
      title="确认发布这个版本？"
      :locked="Boolean(work.busy)"
      @close="confirmation = null"
      ><p>
        页面：<strong>{{ session.draft.title }}</strong>
      </p>
      <p>
        目标环境：<strong>{{ session.environment }}</strong> · 目标版本 {{ session.draft.version }}
      </p>
      <p>当前基线：{{ session.baseline_version }} · 草稿修订 {{ session.revision }}</p>
      <ChangePreview :diff="diff" />
      <p>当前必要检查通过。提交时会再次核对候选、需求、基线与证据有效性。</p>
      <p class="muted">发布后仍需核对服务加载及业务浏览器实际使用。</p>
      <p
        v-if="work.error"
        class="inline-error"
        role="alert"
      >
        {{ work.error }}。可重试同一次操作核对回执，系统不会重复创建发布。
      </p>
      <div class="action-row">
        <button
          class="secondary"
          :disabled="Boolean(work.busy)"
          @click="confirmation = null"
        >
          返回修改</button
        ><button
          class="primary"
          :disabled="Boolean(work.busy)"
          @click="work.run('提交并核对发布', work.confirm)"
        >
          {{ work.busy || "确认发布此版本" }}
        </button>
      </div></ModalDialog
    >
  </section>
</template>

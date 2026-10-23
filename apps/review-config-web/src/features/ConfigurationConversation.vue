<script setup lang="ts">
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import type { Workbench } from "../model/use-workbench";
import { progressLabel, questionOptions } from "../model/presentation";
import { renderMarkdown } from "../ui/markdown";
import Icon from "../ui/Icon.vue";
import forjLogo from "../assets/forj-logo.png";

const { work } = defineProps<{ work: Workbench }>();
const text = computed({
  get: () => work.messageDraft,
  set: (value) => {
    work.setMessageDraft(value);
  },
});
const scroll = ref<HTMLDivElement | null>(null),
  hasNew = ref(false);
let following = true;
const session = computed(() => work.session);
const messages = computed(() => session.value?.chat_messages ?? []);
const committing = computed(() => Boolean(session.value?.pending?.confirmed));
const disabled = computed(
  () =>
    !session.value ||
    !session.value.snapshot ||
    Boolean(session.value.receipt) ||
    session.value.state === "CLOSED" ||
    committing.value,
);
const suggestions = [
  {
    title: "新增审核字段",
    value: "新增注册日期审核，使用行驶证上的注册日期进行比对。",
    icon: "plus",
  },
  { title: "调整已有规则", value: "发动机号不再审核，其他已有规则保持不变。", icon: "settings" },
  {
    title: "核对材料要求",
    value: "帮我梳理当前材料要求，有不明确的业务含义先问我。",
    icon: "image",
  },
] as const;
watch(
  [() => messages.value.length, () => work.running],
  () => {
    if (following) scroll.value?.scrollTo({ top: scroll.value.scrollHeight, behavior: "smooth" });
    else hasNew.value = true;
  },
  { flush: "post" },
);
const scrollKey = `review.workspace.v2.messages.${session.value?.task_id}`;
onMounted(async () => {
  await nextTick();
  if (scroll.value) {
    const stored = sessionStorage.getItem(scrollKey);
    scroll.value.scrollTop = stored === null ? scroll.value.scrollHeight : Number(stored);
    onScroll();
  }
});
onBeforeUnmount(() => {
  if (scroll.value) sessionStorage.setItem(scrollKey, String(scroll.value.scrollTop));
});
function onScroll() {
  const el = scroll.value;
  following = Boolean(el && el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  if (following) hasNew.value = false;
}
async function send(value: string) {
  if (disabled.value || work.busy || !value.trim()) return;
  await work.run(work.running ? "接收更正并更新方案" : "发送说明", async () => {
    await work.send(value);
    if (text.value === value) text.value = "";
  });
}
function keydown(event: KeyboardEvent) {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) {
    event.preventDefault();
    void send(text.value);
  }
}
</script>

<template>
  <section
    class="conversation"
    aria-label="配置对话"
  >
    <header class="panel-heading">
      <h2>与配置助手对话</h2>
      <button
        v-if="work.running && !committing"
        class="text-button"
        :disabled="Boolean(work.busy)"
        @click="work.run('暂停处理', () => work.action('cancel'))"
      >
        暂停
      </button>
    </header>
    <div
      ref="scroll"
      class="conversation-scroll"
      @scroll="onScroll"
    >
      <div
        v-if="!messages.length"
        class="chat-welcome"
      >
        <div class="welcome-mark">
          <img
            :src="forjLogo"
            alt="赋界科技"
          />
        </div>
        <span class="eyebrow">你的审核配置助手</span>
        <p>先描述想改变的业务，我会调查配置、按需引用现场，并解释修改结果。</p>
        <div
          v-if="session"
          class="context-chip"
        >
          {{ session.draft.title }} ·
          {{ session.snapshot ? "已关联保存的现场证据" : "请先关联页面采集再聊天" }}
        </div>
        <button
          v-else
          class="primary welcome-start"
          :disabled="Boolean(work.busy)"
          @click="work.run('读取页面采集', work.openScope)"
        >
          选择已有页面<Icon
            name="arrow"
            :size="17"
          />
        </button>
        <div class="suggestions">
          <button
            v-for="item in suggestions"
            :key="item.title"
            @click="session ? (text = item.value) : work.run('读取页面采集', work.openScope)"
          >
            <Icon :name="item.icon" /><strong>{{ item.title }}</strong
            ><span>{{ item.value }}</span
            ><Icon
              name="chevron"
              :size="15"
            />
          </button>
        </div>
      </div>
      <div
        v-else
        class="message-list"
      >
        <article
          v-for="(message, i) in messages"
          :key="i"
          class="message"
          :class="message.role"
        >
          <div
            v-if="message.role === 'assistant'"
            class="assistant-avatar"
          >
            <img
              :src="forjLogo"
              alt=""
            />
          </div>
          <div class="message-content">
            <template v-if="message.role === 'assistant'"
              ><span class="message-author">配置助手</span
              ><!-- renderMarkdown 禁止原始 HTML、外部图片与危险链接。 -->
              <div
                class="message-markdown"
                v-html="renderMarkdown(message.content)"
            /></template>
            <div v-else>{{ message.content }}</div>
          </div>
        </article>
      </div>
      <div
        v-if="work.running"
        class="agent-progress"
        role="status"
      >
        <span class="thinking-dot" /><span>{{ progressLabel(work.events) }}</span
        ><span class="animated-dots">···</span>
      </div>
      <button
        v-if="session?.pending && !committing && !work.running"
        class="text-button"
        :disabled="Boolean(work.busy)"
        @click="work.run('暂存当前任务', () => work.action('cancel'))"
      >
        暂时无法确认，先暂存
      </button>
      <div
        v-if="session?.pending?.kind === 'browser'"
        class="interaction-card trial-card"
      >
        <Icon
          name="scan"
          :size="26"
        />
        <div>
          <h3>核对配置能否正确读取原页面</h3>
          <p>将通过扩展在原业务标签页执行只读试采集，不需要切换页面。</p>
          <button
            class="primary"
            :disabled="Boolean(work.busy) || work.running"
            @click="work.run('执行只读试采集', work.trial)"
          >
            <Icon
              name="scan"
              :size="17"
            />执行只读试采集
          </button>
        </div>
      </div>
      <div
        v-if="session?.task.can_publish && !committing"
        class="interaction-card success-card"
      >
        <Icon
          name="check"
          :size="23"
        />
        <div>
          <h3>候选配置已通过验证</h3>
          <p>请在右侧检查变更和依据，然后确认发布。</p>
        </div>
      </div>
      <div
        v-if="session?.receipt"
        class="interaction-card success-card"
      >
        <Icon
          name="shield"
          :size="23"
        />
        <div>
          <h3>{{ session.task.label }}</h3>
          <p>发布记录、服务加载、指定浏览器使用分别核对，详见右侧结果。</p>
        </div>
      </div>
      <div
        v-if="!work.running && session?.execution_summary && !session.pending"
        class="execution-summary"
        :class="{ 'inline-error': Boolean(session.error) }"
        :role="session.error ? 'alert' : 'status'"
      >
        <strong>{{ session.execution_summary.title }}</strong>
        <p>{{ session.execution_summary.detail }}</p>
        <details v-if="session.execution_summary.findings?.length">
          <summary>本轮取得的依据</summary>
          <ul>
            <li
              v-for="finding in session.execution_summary.findings"
              :key="finding"
            >
              {{ finding }}
            </li>
          </ul>
        </details>
        <p class="small">{{ session.execution_summary.next_action }}</p>
      </div>
      <div
        v-else-if="session?.error"
        class="inline-error"
        role="alert"
      >
        {{ session.error }}
      </div>
    </div>
    <div class="composer-area">
      <div
        v-if="session?.pending?.kind === 'question'"
        class="interaction-card"
      >
        <span class="eyebrow">只确认这个业务决定</span>
        <h3>{{ session.pending.question }}</h3>
        <div class="option-list">
          <button
            v-for="option in questionOptions(session.pending.options)"
            :key="option"
            class="secondary"
            :disabled="disabled || Boolean(work.busy)"
            @click="send(option)"
          >
            {{ option }}
          </button>
        </div>
        <p>没有默认答案，也可以在下方用自然语言说明。</p>
      </div>

      <button
        v-if="hasNew"
        class="text-button"
        @click="scroll?.scrollTo({ top: scroll.scrollHeight, behavior: 'smooth' })"
      >
        有新进展，回到底部
      </button>
      <div
        v-if="work.referenceDraft.length"
        class="reference-chips"
        aria-label="待发送的资料引用"
      >
        <span
          v-for="reference in work.referenceDraft"
          :key="reference.kind + reference.id"
          >{{ reference.label
          }}<button
            :aria-label="`移除引用${reference.label}`"
            @click="work.removeQuote(reference.id)"
          >
            ×
          </button></span
        >
      </div>
      <form
        class="composer"
        :class="{ inactive: disabled }"
        @submit.prevent="send(text)"
      >
        <textarea
          v-model="text"
          rows="3"
          aria-label="业务要求或问题回答"
          :placeholder="
            session?.receipt
              ? '已发布任务保持只读，可新建后续修改'
              : work.running
                ? '可以补充或更正，系统将停止旧执行并更新方案…'
                : '补充、更正或询问进展…'
          "
          :disabled="disabled"
          @keydown="keydown"
        />
        <div class="composer-toolbar">
          <span
            ><img
              class="composer-brand"
              :src="forjLogo"
              alt=""
            />{{ session?.pending?.kind === "question" ? "补充业务含义" : "审核配置助手" }}</span
          >
          <div>
            <small>Enter 换行 · Ctrl / ⌘ + Enter 发送</small>
            <button
              class="send-button"
              type="submit"
              aria-label="发送消息"
              :disabled="disabled || Boolean(work.busy) || !text.trim()"
            >
              <Icon
                name="arrow"
                :size="21"
              />
            </button>
          </div>
        </div>
      </form>
      <p class="composer-note">
        <Icon
          name="shield"
          :size="12"
        />变更经验证并由你确认后保存；不会自动提交业务审批
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Workbench } from "../model/use-workbench";
import { progressLabel, questionOptions, visibleMessages } from "../model/presentation";
import { renderMarkdown } from "../ui/markdown";
import Icon from "../ui/Icon.vue";
import forjLogo from "../assets/forj-logo.png";

const { work } = defineProps<{work: Workbench}>();
const text = ref("");
const bottom = ref<HTMLDivElement | null>(null);
const session = computed(() => work.session);
const messages = computed(() => session.value ? visibleMessages(session.value) : []);
const committing = computed(() => Boolean(session.value?.pending?.confirmed));
const disabled = computed(() => Boolean(work.busy) || work.running || !session.value || Boolean(session.value.receipt) || committing.value || session.value.pending?.kind === "browser");
const suggestions = [
  {title: "新增审核字段", value: "新增注册日期审核，使用行驶证上的注册日期进行比对。", icon: "plus"},
  {title: "调整已有规则", value: "发动机号不再审核，其他已有规则保持不变。", icon: "settings"},
  {title: "核对材料要求", value: "帮我梳理选中材料对应的审核字段，有不明确的地方先问我。", icon: "image"},
] as const;
watch([() => messages.value.length, () => work.running], () => {
  bottom.value?.scrollIntoView({behavior: "smooth", block: "end"});
}, {flush: "post"});
async function send(value: string) {
  if (disabled.value || !value.trim()) return;
  await work.run("发送消息", async () => { await work.send(value); text.value = ""; });
}
function keydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send(text.value); }
}
</script>

<template>
  <section class="conversation" aria-label="配置对话">
    <div class="conversation-scroll">
      <div v-if="!messages.length" class="chat-welcome">
        <div class="welcome-mark"><img :src="forjLogo" alt="赋界科技"/></div><span class="eyebrow">你的审核配置助手</span>
        <p>从页面字段和图片出发，把业务要求变成可验证的审核配置。</p>
        <div v-if="session" class="context-chip"><span class="status-dot"/>{{ session.snapshot.title }} · 已选 {{ session.selected_fields.length }} 个字段</div>
        <button v-else class="primary welcome-start" :disabled="Boolean(work.busy)" @click="work.run('读取页面采集', work.openScope)">选择已采集的页面<Icon name="arrow" :size="17"/></button>
        <div class="suggestions"><button v-for="item in suggestions" :key="item.title" @click="session ? text = item.value : work.run('读取页面采集', work.openScope)"><Icon :name="item.icon"/><strong>{{ item.title }}</strong><span>{{ item.value }}</span><Icon name="chevron" :size="15"/></button></div>
      </div>
      <div v-else class="message-list"><article v-for="(message, i) in messages" :key="i" class="message" :class="message.role">
        <div v-if="message.role === 'assistant'" class="assistant-avatar"><img :src="forjLogo" alt=""/></div>
        <div class="message-content"><template v-if="message.role === 'assistant'"><span class="message-author">配置助手</span><!-- renderMarkdown 禁止原始 HTML、外部图片与危险链接。 --><div class="message-markdown" v-html="renderMarkdown(message.content)"/></template><div v-else>{{ message.content }}</div></div>
      </article></div>
      <div v-if="work.running" class="agent-progress" role="status"><span class="thinking-dot"/><span>{{ progressLabel(work.events) }}</span><span class="animated-dots">···</span></div>
      <div v-if="session?.pending?.kind === 'question'" class="interaction-card"><span class="eyebrow">需要你确认</span><h3>{{ session.pending.question }}</h3><div class="option-list"><button v-for="option in questionOptions(session.pending.options)" :key="option" class="secondary" :disabled="disabled" @click="send(option)">{{ option }}</button></div><p>也可以在下方用自己的话说明。</p></div>
      <div v-if="session?.pending?.kind === 'browser'" class="interaction-card trial-card"><Icon name="scan" :size="26"/><div><h3>核对配置能否正确读取原页面</h3><p>将通过扩展在原业务标签页执行只读试采集，不需要切换页面。</p><button class="primary" :disabled="Boolean(work.busy) || work.running" @click="work.run('执行只读试采集', work.trial)"><Icon name="scan" :size="17"/>执行只读试采集</button></div></div>
      <div v-if="session?.state === 'READY_FOR_CONFIRMATION' && !committing" class="interaction-card success-card"><Icon name="check" :size="23"/><div><h3>候选配置已通过验证</h3><p>请在右侧检查变更和依据，然后确认保存。</p></div></div>
      <div v-if="session?.receipt" class="interaction-card success-card"><Icon name="shield" :size="23"/><div><h3>{{ session.state === 'EFFECTIVE' ? '审核服务已加载此版本' : '配置已保存到本地' }}</h3><p>{{ session.state === 'EFFECTIVE' ? '可以使用新的页面规则进行审核。' : '完成现有审核作业后重启审核服务，再核对实际加载版本。' }}</p></div></div>
      <div v-if="session?.error" class="inline-error" role="alert">{{ session.error }}</div><div ref="bottom"/>
    </div>
    <div class="composer-area">
      <form class="composer" :class="{inactive: disabled}" @submit.prevent="send(text)">
        <textarea v-model="text" aria-label="业务要求或问题回答" :placeholder="!session ? '先选择页面范围，再开始配置…' : session.receipt ? '此版本已保存，可新建会话继续修改' : '描述需要新增、修改或删除的审核规则…'" :disabled="disabled" @keydown="keydown"/>
        <div class="composer-toolbar"><span><img class="composer-brand" :src="forjLogo" alt=""/>{{ session?.pending?.kind === 'question' ? '补充业务含义' : '审核配置助手' }}</span><div><small>Enter 发送 · Shift + Enter 换行</small>
          <button v-if="work.running && session && !committing" class="send-button stop" type="button" aria-label="停止当前执行" :disabled="Boolean(work.busy)" @click="work.run('停止执行', () => work.action('cancel'))"><Icon name="pause" :size="18"/></button>
          <button v-else class="send-button" type="submit" aria-label="发送消息" :disabled="disabled || !text.trim()"><Icon name="arrow" :size="21"/></button>
        </div></div>
      </form>
      <p class="composer-note"><Icon name="shield" :size="12"/>变更经验证并由你确认后保存；不会自动提交业务审批</p>
    </div>
  </section>
</template>

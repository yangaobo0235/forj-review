<script setup lang="ts">
import { computed } from "vue";
import ConfigurationConversation from "../features/ConfigurationConversation.vue";
import ConfigurationInspector from "../features/ConfigurationInspector.vue";
import SourceDialog from "../features/SourceDialog.vue";
import { useWorkbench, type ScopeSelection } from "../model/use-workbench";
import { displayTime, stateLabels } from "../model/presentation";
import Icon from "../ui/Icon.vue";
import forjLogo from "../assets/forj-logo.png";

const work = useWorkbench();
const session = computed(() => work.session);
const step = computed(() => !session.value ? 0 : session.value.report?.ok || session.value.receipt ? 2 : 1);
function saveScope(selection: ScopeSelection, update: boolean) {
  void work.run(update ? "更新页面范围" : "创建配置会话", () => work.saveScope(selection, update));
}
function newSession() { work.navigate(""); void work.run("读取页面采集", work.openScope); }
</script>

<template>
  <main class="workbench">
    <aside class="sidebar" aria-label="会话导航">
      <div class="brand"><img class="brand-logo" :src="forjLogo" alt="赋界科技"/><div><strong>审核配置工作台</strong><span>页面字段与审核规则</span></div></div>
      <button class="new-session" :disabled="Boolean(work.busy)" @click="newSession"><Icon name="plus" :size="18"/>新建配置会话<span>↗</span></button>
      <div class="history-heading"><span>配置记录</span><button class="icon-button" aria-label="刷新会话记录" :disabled="Boolean(work.busy)" @click="work.run('刷新记录', work.inventory)"><Icon name="refresh" :size="14"/></button></div>
      <nav class="session-history">
        <button v-for="item in work.sessions" :key="item.session_id" :class="{active: item.session_id === work.sessionId}" :disabled="Boolean(work.busy)" @click="work.navigate(item.session_id)">
          <Icon name="chat" :size="17"/><div><strong>{{ item.title || "未命名页面" }}</strong><span>{{ stateLabels[item.state] ?? "配置处理中" }} <i>·</i> {{ displayTime(item.updated_at) }}</span></div>
        </button>
        <div v-if="!work.sessions.length" class="history-empty"><p>还没有配置记录</p><span>从扩展采集页面后开始第一次对话</span></div>
      </nav>
      <div class="sidebar-footer"><span class="connection-dot" :class="{online: work.connected}"/><div><strong>{{ work.connected ? "本地配置服务已连接" : "等待本地配置服务" }}</strong><span>独立工作台 · 仅本机使用</span></div><Icon name="shield" :size="17"/></div>
    </aside>
    <div class="main-workspace">
      <header class="topbar"><div><span class="breadcrumb">工作台 <Icon name="chevron" :size="12"/></span><strong>{{ session?.snapshot.title || "审核配置" }}</strong></div><div><span class="local-badge"><span class="status-dot"/>本地工作空间</span><button class="secondary compact" :disabled="Boolean(work.busy)" @click="work.run('读取页面采集', work.openScope)"><Icon name="pageFields" :size="15"/>页面范围</button></div></header>
      <div class="stepbar"><div v-for="(label, i) in ['选择页面范围', '沟通审核规则', '验证并保存']" :key="label" :class="{current: step === i, complete: step > i}"><span><Icon v-if="step > i" name="check" :size="12"/><template v-else>{{ `0${i + 1}` }}</template></span>{{ label }}<i v-if="i < 2"/></div></div>
      <div v-if="work.error || work.connectionError || work.notice" class="notification" :class="{error: work.error || work.connectionError}" :role="work.error || work.connectionError ? 'alert' : 'status'">
        <span>{{ work.error || work.connectionError || work.notice }}</span><button v-if="work.connectionError" class="text-button" @click="work.run('重新连接', work.inventory)">重新连接</button>
      </div>
      <div class="workspace-content"><ConfigurationConversation :key="work.sessionId" :work="work"/><ConfigurationInspector :work="work"/></div>
    </div>
    <SourceDialog v-if="work.scopeOpen" :error="work.error || work.connectionError" :capture="work.capture" :captures="work.captures" :session="session" :templates="work.templates" :busy="Boolean(work.busy)" :return-focus="work.scopeOpener"
      :on-close="() => work.setScopeOpen(false)" :on-select="id => { void work.run('读取页面采集', () => work.selectCapture(id)); }" :on-save="saveScope"/>
  </main>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import ConfigurationConversation from "../features/ConfigurationConversation.vue";
import ConfigurationInspector from "../features/ConfigurationInspector.vue";
import SourceDialog from "../features/SourceDialog.vue";
import TaskStart from "../features/TaskStart.vue";
import { useWorkbench } from "../model/use-workbench";
import { displayTime, stateLabels } from "../model/presentation";
import Icon from "../ui/Icon.vue";
import forjLogo from "../assets/forj-logo.png";

const work = useWorkbench();
const mobilePane = ref("chat"),
  rightTab = ref("result"),
  navigationCollapsed = ref(localStorage.getItem("review.workspace.v2.navigation") === "collapsed"),
  historyOpen = ref(false),
  filter = ref("");
const session = computed(() => work.session);
const tasks = computed(() =>
  work.sessions.filter((s) =>
    `${s.title} ${s.summary ?? ""} ${s.label ?? ""}`.includes(filter.value),
  ),
);
const step = computed(() => session.value?.task.progress_step ?? 0);
function toggleNavigation() {
  if (window.innerWidth < 1100) historyOpen.value = !historyOpen.value;
  else {
    navigationCollapsed.value = !navigationCollapsed.value;
    localStorage.setItem(
      "review.workspace.v2.navigation",
      navigationCollapsed.value ? "collapsed" : "open",
    );
  }
}
function showPane(pane: string) {
  mobilePane.value = pane;
  if (pane !== "chat") rightTab.value = pane;
}
function newTask() {
  work.navigate("");
  work.capture = null;
  historyOpen.value = false;
}
</script>

<template>
  <main
    class="workbench"
    :class="{ 'history-open': historyOpen, 'navigation-collapsed': navigationCollapsed }"
  >
    <button
      v-if="historyOpen"
      class="navigation-scrim"
      aria-label="关闭任务列表"
      @click="historyOpen = false"
    />
    <aside
      class="sidebar"
      aria-label="修改任务列表"
    >
      <button
        v-if="historyOpen"
        class="text-button navigation-close"
        @click="historyOpen = false"
      >
        关闭任务列表
      </button>
      <div class="brand">
        <img
          :src="forjLogo"
          alt="赋界科技"
        />
        <div><strong>审核配置</strong><span>已有页面修改助手</span></div>
      </div>
      <button
        class="primary full-width"
        :disabled="Boolean(work.busy)"
        @click="newTask"
      >
        <Icon
          name="plus"
          :size="17"
        />新建修改任务
      </button>
      <div class="panel-heading">
        <h2>修改任务</h2>
        <button
          class="icon-button"
          aria-label="刷新任务"
          :disabled="Boolean(work.busy)"
          @click="work.run('刷新任务', work.inventory)"
        >
          <Icon
            name="refresh"
            :size="16"
          />
        </button>
      </div>
      <input
        v-model="filter"
        aria-label="查找修改任务"
        placeholder="查找页面或修改要求"
      />
      <nav class="session-history">
        <button
          v-for="item in tasks"
          :key="item.task_id"
          :class="{ active: item.task_id === work.sessionId }"
          :disabled="Boolean(work.busy)"
          @click="
            work.navigate(item.task_id);
            historyOpen = false;
          "
        >
          <strong>{{ item.title }}</strong
          ><span>{{ item.summary || "查看需求与处理结果" }}</span
          ><small
            >{{ item.label || stateLabels[item.state] }} · {{ displayTime(item.updated_at) }}</small
          >
        </button>
        <p
          v-if="!tasks.length"
          class="muted small"
        >
          还没有匹配的任务。
        </p>
      </nav>
      <p
        class="sidebar-footer"
        :class="{ connected: work.connected }"
        role="status"
      >
        {{ work.connected ? "配置服务已连接" : "等待连接配置服务" }}
      </p>
    </aside>
    <section class="main-workspace">
      <header class="topbar">
        <div class="panel-heading">
          <button
            class="secondary history-toggle"
            :aria-expanded="historyOpen || !navigationCollapsed"
            @click="toggleNavigation"
          >
            任务列表
          </button>
          <div>
            <span class="eyebrow">审核配置 / 修改任务</span>
            <h2 :title="String(session?.draft.title || '开始新的页面修改')">
              {{ session?.draft.title || "开始新的页面修改" }}
            </h2>
            <p
              v-if="session"
              class="context-summary"
            >
              正式基线 {{ session.baseline_version || "读取中" }} ·
              {{ work.source ? `采集 ${displayTime(work.source.created_at)}` : "读取页面资料中" }}
            </p>
          </div>
        </div>
        <div class="topbar-status">
          <span>{{
            session ? `数据库：${session.environment || "读取中"} · 配置模式` : "先关联页面采集"
          }}</span
          ><small v-if="session">{{ session.task.label || stateLabels[session.state] }}</small>
        </div>
      </header>
      <div
        v-if="work.error || work.connectionError || work.notice"
        class="notification"
        :class="{ error: work.error || work.connectionError }"
        :role="work.error || work.connectionError ? 'alert' : 'status'"
      >
        <span>{{ work.error || work.connectionError || work.notice }}</span
        ><button
          v-if="work.connectionError"
          class="text-button"
          @click="work.run('重新连接', work.inventory)"
        >
          重新连接
        </button>
      </div>
      <template v-if="session"
        ><nav
          class="stepbar"
          aria-label="任务阶段"
        >
          <span
            v-for="(label, i) in ['页面资料', '理解与修改', '验证', '发布', '生效核对']"
            :key="label"
            :aria-current="step === i ? 'step' : undefined"
            >{{ i + 1 }} {{ label }}</span
          >
        </nav>
        <nav
          class="mobile-panes"
          aria-label="切换工作区"
        >
          <button
            v-for="pane in [
              { id: 'chat', label: '对话' },
              { id: 'result', label: '结果' },
              { id: 'evidence', label: '资料' },
              { id: 'history', label: '记录' },
            ]"
            :key="pane.id"
            :aria-pressed="mobilePane === pane.id"
            @click="showPane(pane.id)"
          >
            {{ pane.label }}
          </button>
        </nav>
        <div
          class="workspace-content"
          :data-mobile-pane="mobilePane"
        >
          <ConfigurationConversation
            :key="work.sessionId"
            :work="work"
          /><ConfigurationInspector
            :key="work.sessionId"
            v-model:tab="rightTab"
            :work="work"
            @chat="showPane('chat')"
            @message="
              work.messageDraft += (work.messageDraft ? '\n' : '') + $event;
              mobilePane = 'chat';
            "
          />
        </div>
      </template>
      <TaskStart
        v-else-if="!work.sessionId"
        :work="work"
      />
      <p
        v-else
        class="loading-state"
        role="status"
      >
        正在读取任务…
      </p>
    </section>
    <SourceDialog
      v-if="work.scopeOpen"
      :work="work"
    />
  </main>
</template>

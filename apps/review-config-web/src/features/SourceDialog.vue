<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from "vue";
import type { CapturedPage, CaptureSummary, ConfigurationSession, PageTemplate } from "@forj/configuration-contracts";
import type { ScopeSelection } from "../model/use-workbench";
import { displayTime } from "../model/presentation";
import ScopeForm from "./ScopeForm.vue";
import Icon from "../ui/Icon.vue";

const props = defineProps<{
  capture: CapturedPage | null; captures: CaptureSummary[]; session: ConfigurationSession | null;
  templates: PageTemplate[]; busy: boolean; error: string;
  onClose: () => void; onSelect: (id: string) => void; onSave: (selection: ScopeSelection, update: boolean) => void;
  returnFocus?: HTMLElement | null;
}>();
const root = ref<HTMLElement | null>(null);
let previous: Element | null = null;
onMounted(() => { previous = props.returnFocus ?? document.activeElement; root.value?.querySelector<HTMLElement>("button")?.focus(); });
onBeforeUnmount(() => { if (previous instanceof HTMLElement) previous.focus(); });
function keydown(event: KeyboardEvent) {
  if (event.key === "Escape") { event.preventDefault(); props.onClose(); return; }
  if (event.key !== "Tab") return;
  const items = [...(root.value?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary") ?? [])].filter(item => item.getClientRects().length);
  if (!items.length) { event.preventDefault(); return; }
  const first = items[0]!, last = items[items.length - 1]!;
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}
function selectCapture(event: Event) { props.onSelect((event.target as HTMLSelectElement).value); }
</script>

<template>
  <div class="dialog-backdrop"><section ref="root" class="source-dialog" role="dialog" aria-modal="true" aria-labelledby="source-title" @keydown="keydown">
    <header class="dialog-header"><div><span class="eyebrow">第一步 · 确定审核范围</span><h2 id="source-title">从这个页面开始</h2></div><button class="icon-button" aria-label="关闭页面范围" @click="onClose"><Icon name="close"/></button></header>
    <div class="capture-picker"><Icon name="scan"/><label>页面采集<select aria-label="页面采集" :value="capture?.capture_id ?? ''" :disabled="busy" @change="selectCapture"><option value="" disabled>选择最近采集的页面</option><option v-for="item in captures" :key="item.capture_id" :value="item.capture_id">{{ item.title || "未命名页面" }} · {{ displayTime(item.created_at) }}</option></select></label></div>
    <div v-if="error" class="notification error" role="alert">{{ error }}</div>
    <ScopeForm v-if="capture" :key="capture.capture_id" :capture="capture" :templates="templates" :session="session" :busy="busy" :on-save="onSave"/>
    <div v-else class="source-empty"><Icon name="scan" :size="40"/><h3>先采集一个业务页面</h3><p>打开需要配置的审核页面，在浏览器扩展的“配置页面”中点击<br/>“采集并打开配置工作台”。</p></div>
  </section></div>
</template>

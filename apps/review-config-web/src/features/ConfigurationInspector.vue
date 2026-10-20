<script setup lang="ts">
import { computed, ref } from "vue";
import type { Workbench } from "../model/use-workbench";
import { request } from "../api/client";
import { stateLabels } from "../model/presentation";
import Icon from "../ui/Icon.vue";

const { work } = defineProps<{work: Workbench}>();
const tab = ref<"scope" | "changes">("scope");
const caseId = ref(""), caseJson = ref("");
const session = computed(() => work.session);
const busy = computed(() => Boolean(work.busy) || work.running);
const committing = computed(() => Boolean(session.value?.pending?.confirmed));
const fields = computed(() => session.value?.snapshot.fields.filter(field => session.value!.selected_fields.includes(field.candidate_id)) ?? []);
const samples = computed(() => session.value?.snapshot.samples.filter(sample => session.value!.selected_samples.includes(sample.sample_id)) ?? []);
const decisions = computed(() => session.value?.decisions.filter(decision => decision.status === "RESOLVED" && decision.revision === session.value!.revision) ?? []);
async function registerCases() {
  await work.run("登记独立验收样例", async () => {
    const result = await request<{case_set_id: string}>("/case-sets", {case_set_id: caseId.value, suite: JSON.parse(caseJson.value)});
    work.setNotice(`已登记 ${result.case_set_id}，请在对话中告诉助手使用该样例集回放。`);
  });
}
function deleteSession() {
  if (window.confirm("清理这个未保存会话及其执行记录？页面采集记录将保留。")) void work.run("清理会话", () => work.action("delete"));
}
</script>

<template>
  <aside class="inspector" aria-label="配置详情">
    <div class="inspector-header"><span class="eyebrow">配置上下文</span><h2>{{ session ? "当前页面" : "从页面开始" }}</h2></div>
    <div v-if="!session" class="inspector-empty"><div class="outline-icon"><Icon name="file" :size="26"/></div><p>选定页面后，在这里查看材料、字段映射、配置差异和验证结果。</p><ol class="workflow-list"><li><span>1</span><div><strong>选定页面范围</strong><small>选择字段、图片与必传材料</small></div></li><li><span>2</span><div><strong>聊清业务规则</strong><small>助手推理映射，遇到歧义向你确认</small></div></li><li><span>3</span><div><strong>验证并确认保存</strong><small>试采集、样例回放与版本核对</small></div></li></ol></div>
    <template v-else>
      <div class="page-context"><div class="page-icon"><Icon name="file" :size="22"/></div><div><strong>{{ session.snapshot.title || "业务页面" }}</strong><span>草稿版本 {{ session.revision }} · {{ stateLabels[session.state] ?? "配置处理中" }}</span></div></div>
      <div class="inspector-tabs"><button :class="{active: tab === 'scope'}" @click="tab = 'scope'">审核范围</button><button :class="{active: tab === 'changes'}" @click="tab = 'changes'">变更与验证<span v-if="session.report?.ok" class="status-dot"/></button></div>
      <div class="inspector-body">
        <template v-if="tab === 'scope'">
          <div class="section-title"><h3>审核字段 <span>{{ session.selected_fields.length }}</span></h3><button class="text-button" :disabled="busy || Boolean(session.receipt) || committing" @click="work.run('读取页面范围', work.openScope)">调整范围</button></div>
          <div class="selected-fields"><div v-for="field in fields" :key="field.candidate_id"><span>{{ field.label }}</span><small>{{ field.value || "未填写" }}</small></div><p v-if="!fields.length" class="muted small">没有选择页面字段</p></div>
          <div class="section-title"><h3>材料样本 <span>{{ session.selected_samples.length }}</span></h3></div>
          <div class="selected-materials"><a v-for="(sample, i) in samples" :key="sample.sample_id" :href="/^https?:/.test(sample.url) ? sample.url : undefined" target="_blank" rel="noreferrer"><img :src="sample.url" :alt="sample.label || `材料 ${i + 1}`" referrerpolicy="no-referrer"/><span>{{ sample.label || `材料 ${i + 1}` }}</span><em v-if="session.required_samples.includes(sample.sample_id)">必传</em></a></div>
          <div class="section-title"><h3>规则依据</h3></div><div class="decision-list"><p v-for="(decision, i) in decisions" :key="i"><Icon name="check" :size="15"/><span>{{ decision.reason }}</span></p><p v-if="!decisions.length" class="muted small">助手核对后，字段映射和材料依据将显示在这里。</p></div>
        </template>
        <template v-else>
          <div class="validation-heading" :class="{passed: session.report?.ok}"><Icon name="shield" :size="22"/><div><strong>{{ session.report ? session.report.ok ? "当前候选验证通过" : "还有项目需要处理" : "尚未验证" }}</strong><span>依据服务端验证结果展示</span></div></div>
          <div v-for="(item, i) in session.report?.blockers" :key="i" class="blocker">{{ item }}</div><p v-if="!session.report" class="muted small">助手完成草稿、试采集和独立样例回放后，会提交候选配置验证。</p>
          <p v-for="(item, i) in session.report?.decision_summary" :key="i" class="decision-summary"><Icon name="check" :size="14"/>{{ item }}</p>
          <details class="technical-details"><summary>查看完整配置差异</summary><pre>{{ JSON.stringify(session.report?.diff ?? {}, null, 2) }}</pre></details>
          <button class="secondary full-width" :disabled="busy || Boolean(session.receipt) || committing || Boolean(session.pending && session.pending.kind !== 'confirmation')" @click="work.run('验证候选配置', () => work.action('validate'))">重新验证当前草稿</button>
        </template>
        <details class="technical-details"><summary>配置草稿与验收工具</summary><p class="muted small">仅支持系统声明的配置能力。独立样例的预期结果由操作人员登记，助手不能修改评分依据。</p><details><summary>查看配置 JSON</summary><pre>{{ JSON.stringify(session.draft, null, 2) }}</pre></details><label>样例集 ID<input v-model="caseId" placeholder="例如 permit-v2"/></label><label>ReplaySuite JSON<textarea v-model="caseJson" placeholder="粘贴人工确认的验收样例 JSON"/></label><button class="secondary full-width" :disabled="busy || !caseId.trim() || !caseJson.trim()" @click="registerCases">登记不可覆盖的样例集</button></details>
      </div>
      <div class="inspector-footer">
        <button v-if="session.state === 'READY_FOR_CONFIRMATION' && session.report?.ok" class="primary full-width" :disabled="busy || committing" @click="work.run('保存本地版本', work.confirm)"><Icon name="check" :size="17"/>{{ committing ? "保存待对账，请继续执行" : "确认变更并保存本地版本" }}</button>
        <button v-if="session.receipt" class="primary full-width" :disabled="Boolean(work.busy)" @click="work.run('核对实际加载版本', () => work.action('activation'))"><Icon name="refresh" :size="16"/>核对审核服务加载版本</button>
        <template v-else><button v-if="!work.running && ['FAILED', 'INTERRUPTED', 'CANCELLED'].includes(session.turn_status)" class="primary full-width" :disabled="busy" @click="work.run('恢复会话', () => work.action('resume'))">继续执行<Icon name="arrow" :size="17"/></button><button class="text-button subtle" :disabled="busy || committing" @click="deleteSession"><Icon name="trash" :size="14"/>清理未保存会话</button></template>
      </div>
    </template>
  </aside>
</template>

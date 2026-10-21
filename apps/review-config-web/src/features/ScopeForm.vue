<script setup lang="ts">
import { computed, ref } from "vue";
import type { CapturedPage, ConfigurationSession, PageTemplate } from "@forj/configuration-contracts";
import type { ScopeSelection } from "../model/use-workbench";
import Icon from "../ui/Icon.vue";

const props = defineProps<{capture: CapturedPage; templates: PageTemplate[]; session: ConfigurationSession | null; busy: boolean; onSave: (selection: ScopeSelection, update: boolean) => void}>();
const sameSource = props.session?.capture_id === props.capture.capture_id;
const fields = ref<string[]>(sameSource ? [...props.session!.selected_fields] : []);
const samples = ref<string[]>(sameSource ? [...props.session!.selected_samples] : []);
const required = ref<string[]>(sameSource ? [...props.session!.required_samples] : []);
const pageId = ref(String(props.session?.draft.page_id ?? ""));
const rollbackVersion = ref("");
const search = ref("");
const snapshot = computed(() => props.capture.snapshot);
const visible = computed(() => snapshot.value.fields.filter(field => `${field.label} ${field.group_label} ${field.section}`.toLowerCase().includes(search.value.toLowerCase())));
const versions = computed(() => props.templates.find(template => template.page_id === pageId.value)?.versions ?? []);
const editable = computed(() => props.session && !props.session.receipt && !["RUNNING", "QUEUED"].includes(props.session.turn_status) && !props.session.pending?.confirmed);
const toggle = (items: string[], id: string) => items.includes(id) ? items.filter(value => value !== id) : [...items, id];
function toggleSample(id: string) { samples.value = toggle(samples.value, id); required.value = required.value.filter(value => value !== id); }
function toggleAll() { fields.value = fields.value.length === snapshot.value.fields.length ? [] : snapshot.value.fields.map(field => field.candidate_id); }
function save(update: boolean) { if (props.busy || !pageId.value) return; props.onSave({fields: fields.value, samples: samples.value, required: required.value, pageId: pageId.value, rollbackVersion: rollbackVersion.value}, update); }
</script>

<template>
  <div class="source-meta"><strong>{{ snapshot.title || "当前业务页面" }}</strong><span>{{ snapshot.fields.length }} 个字段 · {{ snapshot.samples.length }} 张图片</span><a :href="/^https?:/.test(snapshot.page_url) ? snapshot.page_url : undefined" target="_blank" rel="noreferrer">查看来源 <Icon name="external" :size="13"/></a></div>
  <div v-if="snapshot.diagnostics.length" class="diagnostics"><p v-for="(diagnostic, i) in snapshot.diagnostics" :key="i">{{ diagnostic }}</p></div>
  <div class="scope-target"><label>配置对象<select v-model="pageId" aria-label="配置对象" @change="rollbackVersion = ''"><option value="" disabled>选择已接入页面</option><option v-for="item in templates" :key="item.page_id" :value="item.page_id">{{ item.title }} · {{ item.region }}</option></select></label><label v-if="pageId">操作方式<select v-model="rollbackVersion" aria-label="操作方式"><option value="">创建新版本并修改</option><option v-for="version in versions" :key="version" :value="version">回退到 {{ version }}</option></select></label></div>
  <div class="scope-columns">
    <section class="scope-fields"><div class="section-title"><h3><Icon name="file" :size="17"/>需要审核的字段 <span>{{ fields.length }}</span></h3><button class="text-button" @click="toggleAll">{{ fields.length === snapshot.fields.length ? "取消全选" : "全选" }}</button></div>
      <input v-model="search" class="field-search" placeholder="搜索字段名称或分组" aria-label="搜索字段"/>
      <div class="field-candidates"><label v-for="field in visible" :key="field.candidate_id" class="field-choice" :class="{selected: fields.includes(field.candidate_id)}"><input v-model="fields" type="checkbox" :value="field.candidate_id"/><span><strong>{{ field.label || "未命名字段" }}</strong><small>{{ field.group_label || field.section }} · {{ field.control_kind }}</small></span><em>{{ field.value || "空值" }}</em></label><p v-if="!visible.length" class="muted empty-line">没有匹配的字段</p></div>
    </section>
    <section class="scope-materials"><div class="section-title"><h3><Icon name="image" :size="17"/>用于识别的材料 <span>{{ samples.length }}</span></h3></div><p class="muted small">选择核对样本；必传标记只验证当前配置是否已要求上传。</p>
      <div class="material-candidates"><div v-for="(sample, i) in snapshot.samples" :key="sample.sample_id" class="material-choice" :class="{selected: samples.includes(sample.sample_id)}">
        <a :href="/^https?:/.test(sample.url) ? sample.url : undefined" target="_blank" rel="noreferrer" title="打开材料原图"><img :src="sample.url" :alt="sample.label || `材料 ${i + 1}`" referrerpolicy="no-referrer" loading="lazy"/></a>
        <label><input type="checkbox" :checked="samples.includes(sample.sample_id)" @change="toggleSample(sample.sample_id)"/><strong>{{ sample.label || `材料 ${i + 1}` }}</strong></label>
        <label class="required-choice"><input v-model="required" type="checkbox" :value="sample.sample_id" :disabled="!samples.includes(sample.sample_id)"/>此类材料必传</label>
      </div><div v-if="!snapshot.samples.length" class="mini-empty"><Icon name="image"/><p>此页面没有可分析的图片</p></div></div>
    </section>
  </div>
  <footer class="dialog-footer"><p><Icon name="shield" :size="16"/>仅采集和验证，不提交业务审批</p><div><button v-if="editable" class="secondary" :disabled="busy" @click="save(true)">更新当前会话现场</button><button class="primary" :disabled="busy || !pageId" @click="save(false)">{{ busy ? "正在处理…" : "开始新的配置会话" }}<Icon name="arrow" :size="17"/></button></div></footer>
</template>

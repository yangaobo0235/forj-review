<script setup lang="ts">
import { computed } from "vue";
import { fieldChanges } from "../model/field-diff";
const props = defineProps<{ diff: unknown }>();
type Value = Record<string, unknown> | null;
const changes = computed(() => fieldChanges(props.diff));
const optionLabels: Record<string, string> = {
  material_policy: "材料要求与证据",
  replacement_policy: "政策参数",
  presentation: "审核展示",
  actions: "回填动作",
  rule_bindings: "规则依赖",
  materials: "材料声明",
  sections: "业务分区",
  page_groups: "图片分组",
  identity_anchors: "页面身份锚点",
  record_identity_fields: "记录身份字段",
};
const options = computed(() => {
  const diff = props.diff as {
    page_options?: {
      changed?: Record<string, { before: unknown; after: unknown }>;
      added?: Record<string, unknown>;
      removed?: Record<string, unknown>;
    };
  } | null;
  const d = diff?.page_options;
  return [
    ...Object.entries(d?.changed ?? {}).map(([key, c]) => ({ key, ...c })),
    ...Object.entries(d?.added ?? {}).map(([key, after]) => ({ key, before: null, after })),
    ...Object.entries(d?.removed ?? {}).map(([key, before]) => ({ key, before, after: null })),
  ];
});
function name(value: Value) {
  return value ? String(value.label ?? "已有配置") : "未配置";
}
</script>
<template>
  <section aria-label="最终变更预览">
    <h3>修改前后</h3>
    <article
      v-for="change in changes"
      :key="change.page + change.key"
      class="change-item"
    >
      <div class="panel-heading">
        <strong>{{ change.label }}</strong
        ><span class="tag">{{ change.kind }}</span>
      </div>
      <p class="muted small">{{ change.detail }}</p>
      <div class="before-after">
        <div>
          <small>修改前</small>
          <p>{{ name(change.before) }}</p>
        </div>
        <div>
          <small>修改后</small>
          <p>{{ name(change.after) }}</p>
        </div>
      </div>
      <details>
        <summary>查看该项完整差异</summary>
        <pre>{{ JSON.stringify({ 修改前: change.before, 修改后: change.after }, null, 2) }}</pre>
      </details>
    </article>
    <article
      v-for="option in options"
      :key="option.key"
      class="change-item"
    >
      <strong>{{ optionLabels[option.key] || option.key }}</strong>
      <p class="muted small">本项业务配置发生变化，请核对新旧内容及影响。</p>
      <details>
        <summary>查看修改前与修改后</summary>
        <div class="before-after">
          <pre>{{ JSON.stringify(option.before, null, 2) }}</pre>
          <pre>{{ JSON.stringify(option.after, null, 2) }}</pre>
        </div>
      </details>
    </article>
    <p
      v-if="!changes.length && !options.length"
      class="muted"
    >
      尚无业务配置差异。Agent调查或编辑后会在这里展示。
    </p>
  </section>
</template>

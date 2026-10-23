<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { request } from "../api/client";
import type { Workbench } from "../model/use-workbench";

type Expectation = {
  recommendation: string;
  comparisons?: Record<string, string>;
  checks?: Record<string, string>;
  capabilities?: Record<string, string>;
};
type Suite = {
  schema_version: string;
  cases: Array<{
    name: string;
    request: Record<string, unknown>;
    batch: unknown;
    expect: Expectation;
  }>;
};
const { work } = defineProps<{ work: Workbench }>();
const source = ref(""),
  caseId = ref(""),
  confirmed = ref(false),
  suite = ref<Suite | null>(null);
const locked = computed(
  () =>
    Boolean(work.busy) ||
    work.running ||
    work.session?.state === "CLOSED" ||
    Boolean(work.session?.receipt || work.session?.pending?.confirmed),
);
const statusLabels: Record<string, string> = {
  PASS: "通过",
  REVIEW_REQUIRED: "需人工复核",
  MATCH: "一致",
  CONFLICT: "冲突",
  INSUFFICIENT: "证据不足",
  READY: "可执行",
  SKIPPED: "已跳过",
  BLOCKED: "受阻",
  NOT_CONFIGURED: "未配置",
  SUCCEEDED: "执行成功",
  FAILED: "执行失败",
};
const fieldLabels = computed(() =>
  Object.fromEntries(
    ((work.session?.draft.fields ?? []) as Array<{ key: string; label: string }>).map((f) => [
      f.key,
      f.label,
    ]),
  ),
);
function inputFacts(c: Suite["cases"][number]) {
  const page = c.request.page_fields as Record<string, unknown> | undefined;
  const batch = c.batch as {
    observations?: Array<{
      field: string;
      value: unknown;
      document_type?: string;
      business_scope?: string;
    }>;
  };
  return [
    ...Object.entries(page ?? {}).map(([key, value]) => ({
      label: fieldLabels.value[key] ?? key,
      value,
      source: "业务页面",
    })),
    ...(batch.observations ?? []).map((o) => ({
      label: fieldLabels.value[o.field] ?? o.field,
      value: o.value,
      source: [o.document_type, o.business_scope].filter(Boolean).join(" · ") || "固定材料证据",
    })),
  ];
}
async function reuse() {
  if (!source.value || locked.value) return;
  await work.run("回放已有独立预期", async () => {
    await work.manual("replay", { case_set_id: source.value });
    work.setNotice("已使用登记的固定输入与独立预期回放。源样例未改动；可继续完整验证。");
  });
}
watch(
  () => [work.session?.task_id, work.session?.draft.version, source.value],
  () => {
    suite.value = null;
    confirmed.value = false;
  },
);
async function load() {
  await work.run("读取独立验收样例", async () => {
    const sid = work.session?.task_id,
      version = work.session?.draft.version,
      sourceId = source.value;
    const value = await request<Suite>(`/case-sets?case_set_id=${encodeURIComponent(sourceId)}`);
    if (
      sid !== work.session?.task_id ||
      version !== work.session?.draft.version ||
      sourceId !== source.value
    )
      return;
    value.cases = value.cases.filter((c) => c.request.page_id === work.session?.draft.page_id);
    if (!value.cases.length) throw new Error("此样例集不包含当前页面，请选择对应业务的样例集");
    for (const c of value.cases) c.request.profile_version = work.session!.draft.version;
    suite.value = value;
    confirmed.value = false;
  });
}
async function freeze() {
  if (!suite.value || !confirmed.value) return;
  await work.run("确认并冻结验收预期", async () => {
    const result = await request<{ case_set_id: string }>("/case-sets", {
      case_set_id: caseId.value,
      suite: suite.value,
    });
    await work.inventory();
    confirmed.value = false;
    work.setNotice(
      `已冻结 ${result.case_set_id}，后续修改需登记新版本。可在对话中要求使用此样例集回放。`,
    );
  });
}
</script>

<template>
  <section aria-label="独立验收预期">
    <h3>独立验收预期</h3>
    <p class="muted small">
      从已登记样例读取固定输入，逐项核对业务预期。目标版本为
      {{ work.session?.draft.version }}；助手不能修改冻结样例。
    </p>
    <label
      >参考样例集<select
        v-model="source"
        :disabled="locked"
      >
        <option value="">请选择对应业务</option>
        <option
          v-for="id in work.caseSets"
          :key="id"
        >
          {{ id }}
        </option>
      </select></label
    >
    <button
      class="secondary"
      :disabled="locked || !source"
      @click="load"
    >
      读取输入与预期
    </button>
    <button
      class="secondary"
      :disabled="locked || !source"
      @click="reuse"
    >
      使用已有独立预期回放
    </button>
    <template v-if="suite">
      <article
        v-for="c in suite.cases"
        :key="c.name"
        class="blocker"
      >
        <strong>{{ c.name }}</strong>
        <div
          v-for="(fact, index) in inputFacts(c)"
          :key="index"
          class="result-row"
        >
          <span
            >{{ fact.label }}<small class="muted"> · {{ fact.source }}</small></span
          ><strong>{{ fact.value ?? "缺失" }}</strong>
        </div>
        <details>
          <summary>核对固定输入与材料事实</summary>
          <pre>{{ JSON.stringify({ request: c.request, batch: c.batch }, null, 2) }}</pre>
        </details>
        <label
          >整单预期<select
            v-model="c.expect.recommendation"
            :disabled="locked"
            @change="confirmed = false"
          >
            <option value="PASS">通过</option>
            <option value="REVIEW_REQUIRED">人工复核</option>
          </select></label
        >
        <template
          v-for="kind in ['comparisons', 'checks', 'capabilities'] as const"
          :key="kind"
        >
          <label
            v-for="(_value, key) in c.expect[kind]"
            :key="key"
            >{{ kind === "comparisons" ? "字段" : kind === "checks" ? "规则" : "能力" }} ·
            {{ fieldLabels[key] ?? key }}
            <select
              v-model="c.expect[kind]![key]"
              :disabled="locked"
              @change="confirmed = false"
            >
              <option
                v-for="value in kind === 'capabilities'
                  ? ['READY', 'SKIPPED', 'BLOCKED', 'NOT_CONFIGURED', 'SUCCEEDED', 'FAILED']
                  : kind === 'checks'
                    ? ['MATCH', 'CONFLICT', 'INSUFFICIENT']
                    : ['MATCH', 'CONFLICT', 'REVIEW_REQUIRED']"
                :key="value"
                :value="value"
              >
                {{ statusLabels[value] }}
              </option>
            </select>
          </label>
        </template>
        <details>
          <summary>其余冻结预期与覆盖范围</summary>
          <pre>{{ JSON.stringify(c.expect, null, 2) }}</pre>
        </details>
      </article>
      <label
        >新样例集 ID<input
          v-model="caseId"
          placeholder="小写字母、数字、下划线、短横线"
          :disabled="locked"
      /></label>
      <label
        ><input
          v-model="confirmed"
          type="checkbox"
          :disabled="locked"
        />我已核对输入、业务预期和覆盖范围，确认冻结</label
      >
      <button
        class="secondary full-width"
        :disabled="locked || !confirmed || !caseId.trim()"
        @click="freeze"
      >
        确认并冻结独立预期
      </button>
    </template>
  </section>
</template>

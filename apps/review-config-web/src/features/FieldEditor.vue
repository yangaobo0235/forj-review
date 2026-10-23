<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { Workbench } from "../model/use-workbench";
import { request } from "../api/client";

interface Evidence {
  material: string;
  field: string;
  scope: string;
}
interface PageField {
  key: string;
  label: string;
  aliases: string[];
  section: string;
  page_section: string;
  locator_id: string;
  locator_name: string;
  occurrence: number;
  normalizer: string;
  mode: string | null;
  evidence: Evidence[];
}
interface Material {
  document_type: string;
  label: string;
}
const { work } = defineProps<{ work: Workbench }>();
const session = computed(() => work.session!);
const pageFields = computed(() => (session.value.draft.fields ?? []) as PageField[]);
const materials = computed(() => (session.value.draft.materials ?? []) as Material[]);
const sections = computed(
  () => (session.value.draft.sections ?? []) as Array<{ key: string; title: string }>,
);
const locked = computed(
  () =>
    work.session?.state === "CLOSED" ||
    Boolean(work.busy) ||
    work.running ||
    Boolean(session.value.receipt) ||
    Boolean(session.value.pending?.confirmed) ||
    Boolean(session.value.pending && session.value.pending.kind !== "confirmation"),
);
const editing = ref(false),
  originalKey = ref("");
const comparisonConfigurable = computed(
  () => session.value.comparison_configurable_fields?.includes(originalKey.value) === true,
);
const form = reactive({
  key: "",
  label: "",
  aliases: "",
  section: "",
  page_section: "unknown",
  locator_id: "",
  locator_name: "",
  occurrence: 1,
  normalizer: "text",
  mode: "PARALLEL" as string | null,
});
const evidence = ref<Evidence[]>([]);
const fieldMappings = reactive<Record<string, string>>({});
const materialMappings = reactive<Record<string, string>>({});
const caseSet = ref("");
const confirmedRequirements = ref<string[]>([]);
const verificationResults = computed(() =>
  Object.entries(session.value.tool_results ?? {}).filter(
    ([, result]) =>
      (result.kind === "collection_trial" && result.binding?.revision === session.value.revision) ||
      (result.case_set_id && result.revision === session.value.revision),
  ),
);
const selectedFields = computed(() =>
  (session.value.snapshot?.fields ?? []).filter((f) =>
    session.value.reference_fields.includes(f.candidate_id),
  ),
);
const selectedSamples = computed(() =>
  (session.value.snapshot?.samples ?? []).filter((s) =>
    session.value.reference_samples.includes(s.sample_id),
  ),
);
watch(
  () => [session.value.task_id, session.value.revision],
  () => {
    editing.value = false;
    originalKey.value = "";
    evidence.value = [];
    confirmedRequirements.value = [];
    for (const key of Object.keys(fieldMappings)) delete fieldMappings[key];
    for (const key of Object.keys(materialMappings)) delete materialMappings[key];
    for (const candidate of selectedFields.value) {
      const matches = pageFields.value.filter((f) => f.aliases.includes(candidate.label));
      fieldMappings[candidate.candidate_id] = matches.length === 1 ? matches[0]!.key : "";
    }
    for (const sample of selectedSamples.value) materialMappings[sample.sample_id] = "";
    for (const decision of session.value.decisions) {
      if (decision.status !== "RESOLVED") continue;
      if (
        decision.candidate_id &&
        decision.field_key &&
        pageFields.value.some((f) => f.key === decision.field_key)
      )
        fieldMappings[decision.candidate_id] = decision.field_key;
      if (decision.sample_id && decision.material)
        materialMappings[decision.sample_id] = decision.material;
    }
  },
  { immediate: true },
);
function edit(field?: PageField) {
  originalKey.value = field?.key ?? "";
  Object.assign(form, {
    key: field?.key ?? "",
    label: field?.label ?? "",
    aliases: field?.aliases.join("\n") ?? "",
    section: field?.section ?? sections.value[0]?.key ?? "",
    page_section: field?.page_section ?? sections.value[0]?.key ?? "unknown",
    locator_id: field?.locator_id ?? "",
    locator_name: field?.locator_name ?? "",
    occurrence: field?.occurrence ?? 1,
    normalizer: field?.normalizer ?? "text",
    mode: field ? field.mode : "PARALLEL",
  });
  evidence.value = field ? field.evidence.map((e) => ({ ...e })) : [];
  editing.value = true;
}
function save() {
  void work.run("保存字段草稿", async () => {
    const dependent =
      comparisonConfigurable.value ||
      (session.value.field_dependencies?.[originalKey.value] ?? []).length > 0;
    const value = dependent
      ? {
          key: form.key,
          label: form.label,
          aliases: form.aliases
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
          locator_id: form.locator_id,
          locator_name: form.locator_name,
          occurrence: form.occurrence,
        }
      : {
          ...form,
          aliases: form.aliases
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
          evidence: evidence.value,
        };
    const operations: Array<{ op: string; value: Record<string, unknown> }> = [
      { op: "upsert_field", value },
    ];
    if (comparisonConfigurable.value && form.mode !== "SYSTEM")
      operations.push({
        op: "set_field_comparison",
        value: {
          key: form.key,
          mode: form.mode,
          normalizer: form.normalizer,
          evidence: evidence.value,
        },
      });
    await work.manual("draft", { operations });
    editing.value = false;
  });
}
function remove(field: PageField) {
  if (!window.confirm(`从审核配置删除“${field.label}”？`)) return;
  void work.run("删除字段", () =>
    work.manual("draft", { operations: [{ op: "remove_field", value: { key: field.key } }] }),
  );
}
function mappings() {
  void work.run("确认现场映射", () =>
    work.manual("mappings", {
      fields: Object.fromEntries(Object.entries(fieldMappings).filter(([, value]) => value)),
      materials: Object.fromEntries(Object.entries(materialMappings).filter(([, value]) => value)),
      requirements: confirmedRequirements.value,
    }),
  );
}
function exportCandidate() {
  void work.run("导出候选与试采集证明", async () => {
    const value = await request(`/tasks/${session.value.task_id}/export`);
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${session.value.draft.page_id}-candidate.json`;
    link.click();
    URL.revokeObjectURL(url);
  });
}
</script>

<template>
  <section
    class="field-editor"
    aria-label="手动字段维护"
  >
    <p class="muted small">
      手动编辑无需调用模型。页面身份、材料类型、业务规则和回填权限由开发维护。
    </p>
    <p
      v-if="session.activation_only"
      class="muted"
    >
      当前为历史回退，只切换版本，不修改内容。
    </p>
    <button
      class="secondary full-width"
      :disabled="locked || session.activation_only"
      @click="edit()"
    >
      新增审核字段
    </button>
    <div
      v-for="field in pageFields"
      :key="field.key"
      class="field-editor-row"
    >
      <strong>{{ field.label }}</strong
      ><small>{{ field.key }}</small>
      <p
        v-if="session.field_dependencies?.[field.key]?.length"
        class="muted small"
      >
        依赖：{{ session.field_dependencies[field.key]!.join("、") }}
      </p>
      <p
        v-if="session.comparison_configurable_fields?.includes(field.key)"
        class="muted small"
      >
        当前仅检查页面输入，可在编辑中设置材料比较。
      </p>
      <div class="field-editor-actions">
        <button
          class="text-button"
          :disabled="locked || session.activation_only"
          @click="edit(field)"
        >
          编辑</button
        ><button
          class="text-button"
          :disabled="
            locked ||
            session.activation_only ||
            (Boolean(session.field_dependencies?.[field.key]?.length) &&
              !session.comparison_configurable_fields?.includes(field.key))
          "
          @click="remove(field)"
        >
          删除
        </button>
      </div>
    </div>
    <form
      v-if="editing"
      class="field-editor-form"
      @submit.prevent="save"
    >
      <h3>{{ originalKey ? "修改字段" : "新增字段" }}</h3>
      <label
        >字段标识<input
          v-model="form.key"
          required
          :readonly="Boolean(originalKey)"
          placeholder="例如 vehicle.engine_no"
      /></label>
      <label
        >显示名称<input
          v-model="form.label"
          required
      /></label>
      <label
        >页面别名（每行一个）<textarea
          v-model="form.aliases"
          required
        />
      </label>
      <label
        >审核分区<select v-model="form.section">
          <option
            v-for="section in sections"
            :key="section.key"
            :value="section.key"
          >
            {{ section.title }}
          </option>
        </select></label
      >
      <label
        >页面区域<select v-model="form.page_section">
          <option value="unknown">通用表单区域</option>
          <option
            v-for="section in sections"
            :key="section.key"
            :value="section.key"
          >
            {{ section.title }}
          </option>
        </select></label
      >
      <details>
        <summary>精确定位</summary>
        <label>控件 ID<input v-model="form.locator_id" /></label
        ><label>控件名称<input v-model="form.locator_name" /></label
        ><label
          >同名控件序号<input
            v-model.number="form.occurrence"
            type="number"
            min="1"
            required
        /></label>
      </details>
      <template v-if="!session.field_dependencies?.[originalKey]?.length || comparisonConfigurable">
        <label
          >比较方式<select v-model="form.mode">
            <option
              v-if="comparisonConfigurable"
              value="SYSTEM"
            >
              仅页面输入（保持现状）
            </option>
            <option
              v-if="form.mode === null"
              :value="null"
            >
              保留现有比较方式
            </option>
            <option value="PARALLEL">多来源核对</option>
            <option value="SINGLE_SOURCE">单一来源</option>
            <option value="AVAILABLE_EVIDENCE">可用证据</option>
            <option value="PAGE_AUXILIARY">页面辅助核对</option>
          </select></label
        >
        <label
          >规范化方式<select v-model="form.normalizer">
            <option
              v-for="kind in [
                'default',
                'text',
                'identifier',
                'vin',
                'plate',
                'date',
                'amount',
                'party_name',
                'phone',
                'invoice_number',
                'vehicle_type',
                'engine_model',
                'fuel_type',
                'fuel_type_exact',
                'jiefang_flag',
                'emission_standard',
              ]"
              :key="kind"
              :value="kind"
            >
              {{ kind }}
            </option>
          </select></label
        >
        <fieldset
          v-for="(source, index) in evidence"
          :key="index"
        >
          <legend>材料来源 {{ index + 1 }}</legend>
          <label
            >材料<select
              v-model="source.material"
              required
            >
              <option
                value=""
                disabled
              >
                选择已有材料
              </option>
              <option
                v-for="material in materials"
                :key="material.document_type"
                :value="material.document_type"
              >
                {{ material.label }}
              </option>
            </select></label
          ><label
            >材料字段<input
              v-model="source.field"
              required
              placeholder="例如 vehicle.engine_no" /></label
          ><label
            >材料分区<select
              v-model="source.scope"
              required
            >
              <option
                v-for="scope in session.draft.scopes as string[]"
                :key="scope"
                :value="scope"
              >
                {{ scope }}
              </option>
            </select></label
          ><button
            type="button"
            class="text-button"
            @click="evidence.splice(index, 1)"
          >
            移除此来源
          </button>
        </fieldset>
        <button
          type="button"
          class="text-button"
          @click="
            evidence.push({
              material: '',
              field: '',
              scope: (session.draft.scopes as string[])[0] ?? '',
            })
          "
        >
          添加材料来源
        </button>
      </template>
      <button
        class="primary full-width"
        :disabled="locked || session.activation_only"
      >
        保存草稿</button
      ><button
        type="button"
        class="text-button"
        @click="editing = false"
      >
        取消编辑
      </button>
    </form>
    <details class="manual-verification">
      <summary>现场映射与独立验证</summary>
      <label
        v-for="requirement in session.requirements?.filter(
          (r) => (r.status ?? 'ACTIVE') === 'ACTIVE',
        )"
        :key="requirement.requirement_id"
        ><span
          ><input
            v-model="confirmedRequirements"
            type="checkbox"
            :value="requirement.requirement_id"
            :disabled="locked"
          />我已核对并完成：{{ requirement.text }}</span
        ></label
      >
      <label
        v-for="field in selectedFields"
        :key="field.candidate_id"
        >{{ field.label
        }}<select
          v-model="fieldMappings[field.candidate_id]"
          :disabled="locked"
        >
          <option value="">请选择审核字段</option>
          <option
            v-for="target in pageFields"
            :key="target.key"
            :value="target.key"
          >
            {{ target.label }} · {{ target.key }}
          </option>
        </select></label
      >
      <label
        v-for="sample in selectedSamples"
        :key="sample.sample_id"
        >{{ sample.label || sample.sample_id
        }}<select
          v-model="materialMappings[sample.sample_id]"
          :disabled="locked"
        >
          <option value="">请选择已有材料</option>
          <option
            v-for="material in materials"
            :key="material.document_type"
            :value="material.document_type"
          >
            {{ material.label }}
          </option>
        </select></label
      >
      <button
        class="secondary full-width"
        :disabled="locked"
        @click="mappings"
      >
        确认现场映射
      </button>
      <button
        class="secondary full-width"
        :disabled="locked"
        @click="work.run('创建只读试采集任务', () => work.manual('trial'))"
      >
        创建只读试采集任务
      </button>
      <button
        v-if="session.pending?.kind === 'browser'"
        class="primary full-width"
        :disabled="Boolean(work.busy) || work.running"
        @click="work.run('只读试采集', work.trial)"
      >
        执行只读试采集
      </button>
      <label
        >独立样例集<select v-model="caseSet">
          <option
            value=""
            disabled
          >
            请选择已登记样例
          </option>
          <option
            v-for="id in work.caseSets"
            :key="id"
            :value="id"
          >
            {{ id }}
          </option>
        </select></label
      >
      <button
        class="secondary full-width"
        :disabled="locked || !caseSet"
        @click="work.run('执行独立回放', () => work.manual('replay', { case_set_id: caseSet }))"
      >
        执行独立回放
      </button>
      <p
        v-for="[id, result] in verificationResults"
        :key="id"
        class="muted small"
      >
        {{ result.case_set_id ? `回放 ${result.case_set_id}` : "只读试采集" }}：{{
          result.expires_at && Date.parse(result.expires_at) <= Date.now()
            ? "已过期，请重新采集"
            : result.ok
              ? "通过"
              : "未通过，请检查候选验证结果"
        }}
      </p>
      <button
        class="secondary full-width"
        :disabled="locked"
        @click="work.run('验证候选', () => work.action('validate'))"
      >
        验证当前候选
      </button>
      <button
        class="text-button"
        :disabled="Boolean(work.busy) || work.running"
        @click="exportCandidate"
      >
        导出候选与试采集证明
      </button>
    </details>
  </section>
</template>

<style scoped>
.field-editor {
  display: grid;
  gap: 12px;
}
.field-editor-row {
  border-bottom: 1px solid var(--border);
  padding: 9px 0;
}
.field-editor-row small {
  display: block;
  color: var(--muted);
  overflow-wrap: anywhere;
  margin-top: 5px;
}
.field-editor-actions {
  display: flex;
  gap: 16px;
  margin-top: 8px;
}
.field-editor-form,
.manual-verification {
  display: grid;
  gap: 12px;
}
.field-editor label {
  display: grid;
  gap: 6px;
  margin: 9px 0;
  font-size: 12px;
}
.field-editor input,
.field-editor select,
.field-editor textarea {
  width: 100%;
}
.field-editor textarea {
  min-height: 65px;
}
.field-editor summary {
  cursor: pointer;
  font-size: 12px;
  margin: 12px 0;
}
.manual-verification button {
  margin: 6px 0;
}
</style>

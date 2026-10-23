<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Workbench } from "../model/use-workbench";

const { work } = defineProps<{ work: Workbench }>();
const selected = ref("material_policy"),
  text = ref("");
const options = [
  ["material_policy", "材料要求与证据"],
  ["replacement_policy", "地区政策"],
  ["rule_bindings", "已有规则开关"],
  ["presentation", "审核展示"],
  ["actions", "回填动作与字段映射"],
  ["page_groups", "图片分组"],
  ["sections", "页面分区"],
  ["identity_anchors", "页面身份锚点"],
  ["record_identity_fields", "记录身份字段"],
];
const locked = computed(
  () =>
    work.session?.state === "CLOSED" ||
    Boolean(work.busy) ||
    work.running ||
    Boolean(work.session?.receipt) ||
    Boolean(work.session?.pending?.confirmed),
);
watch(
  () => [selected.value, work.session?.revision, work.session?.task_id],
  () => {
    text.value = JSON.stringify(work.session?.page_options?.[selected.value] ?? null, null, 2);
  },
  { immediate: true },
);
async function save() {
  await work.run("修改页面选项", async () => {
    await work.manual("draft", {
      operations: [{ op: "set_page_options", value: { [selected.value]: JSON.parse(text.value) } }],
    });
  });
}
</script>

<template>
  <section aria-label="页面配置选项">
    <p class="muted small">
      仅组合框架已实现的能力。保存后需重新试采集、回放并确认发布。回填授权仍需字段单独声明。
    </p>
    <label
      >配置项<select
        v-model="selected"
        :disabled="locked"
      >
        <option
          v-for="[key, label] in options"
          :key="key"
          :value="key"
        >
          {{ label }}
        </option>
      </select></label
    >
    <label
      >当前配置<textarea
        v-model="text"
        rows="22"
        :disabled="locked"
        spellcheck="false"
      />
    </label>
    <button
      class="secondary full-width"
      :disabled="locked"
      @click="save"
    >
      保存到草稿
    </button>
  </section>
</template>

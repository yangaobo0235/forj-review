<script setup lang="ts">
import { ref } from "vue";
import { captureAndOpenWorkbench } from "./bridge";
import Icon from "../../shared/ui/Icon.vue";
const busy = ref(false),
  error = ref(""),
  progress = ref("");
async function capture() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await captureAndOpenWorkbench((text) => {
      progress.value = text;
    });
    error.value = result.problems.length
      ? `现场已保留，采集提示：${result.problems.join("；")}`
      : "";
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "采集失败";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="capture-launcher">
    <button
      class="capture-button"
      title="采集当前页面的字段和图片，并打开聊天配置工作台"
      aria-label="采集并打开配置工作台"
      :disabled="busy"
      @click="capture"
    >
      <Icon
        :name="busy ? 'clock' : 'external'"
        :size="15"
      />{{ busy ? progress : "配置页面" }}
    </button>
    <p
      v-if="error"
      class="capture-error"
      role="alert"
    >
      {{ error }}
    </p>
  </div>
</template>

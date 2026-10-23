<script setup lang="ts">
import type { Workbench } from "../model/use-workbench";
import ModalDialog from "../ui/ModalDialog.vue";
import CapturePicker from "./CapturePicker.vue";

const { work } = defineProps<{ work: Workbench }>();
</script>

<template>
  <ModalDialog
    title="关联业务页面现场"
    :return-focus="work.scopeOpener"
    :locked="Boolean(work.busy)"
    @close="work.setScopeOpen(false)"
  >
    <p class="eyebrow">保留当前需求与候选</p>
    <p class="muted">
      从扩展采集实际业务页面，然后在这里关联。无需选择所有字段或图片；Agent会按任务调查。
    </p>
    <CapturePicker :work="work" />
    <p class="muted small">更换现场会保留旧证据和修改要求，受影响的映射与验证需重新核对。</p>
    <p
      v-if="work.error"
      role="alert"
      class="inline-error"
    >
      {{ work.error }}
    </p>
    <footer class="action-row">
      <button
        class="secondary"
        @click="work.setScopeOpen(false)"
      >
        稍后关联</button
      ><button
        class="primary"
        :disabled="Boolean(work.busy) || !work.capture"
        @click="work.run('关联现场', work.attachCapture)"
      >
        关联并继续
      </button>
    </footer>
  </ModalDialog>
</template>

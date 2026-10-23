<script setup lang="ts">
import { computed, ref } from "vue";
import type { ConfigurationSnapshot } from "@forj/configuration-contracts";
import ModalDialog from "../ui/ModalDialog.vue";
const { samples, initial } = defineProps<{
  samples: ConfigurationSnapshot["samples"];
  initial: number;
}>();
const emit = defineEmits<{ close: [] }>();
const index = ref(initial),
  scale = ref(1),
  rotation = ref(0),
  failed = ref(false);
const offset = ref({ x: 0, y: 0 });
let drag: { x: number; y: number; left: number; top: number } | null = null;
const sample = computed(() => samples[index.value]!);
function move(delta: number) {
  index.value = (index.value + delta + samples.length) % samples.length;
  scale.value = 1;
  rotation.value = 0;
  offset.value = { x: 0, y: 0 };
  failed.value = false;
}
function start(event: PointerEvent) {
  if (!(event.target instanceof HTMLImageElement)) return;
  event.preventDefault();
  event.target.setPointerCapture(event.pointerId);
  drag = { x: event.clientX, y: event.clientY, left: offset.value.x, top: offset.value.y };
}
function pan(event: PointerEvent) {
  if (drag)
    offset.value = { x: drag.left + event.clientX - drag.x, y: drag.top + event.clientY - drag.y };
}
</script>
<template>
  <ModalDialog
    :title="sample.label || '未确认材料名称'"
    @close="emit('close')"
  >
    <div
      class="image-viewer"
      @keydown.left.prevent="move(-1)"
      @keydown.right.prevent="move(1)"
    >
      <div class="viewer-toolbar">
        <button
          class="secondary"
          aria-label="上一张图片"
          @click="move(-1)"
        >
          上一张
        </button>
        <span>{{ index + 1 }} / {{ samples.length }}</span>
        <button
          class="secondary"
          aria-label="下一张图片"
          @click="move(1)"
        >
          下一张
        </button>
        <button
          class="secondary"
          :disabled="scale >= 4"
          @click="scale = Math.min(4, scale + 0.25)"
        >
          放大
        </button>
        <button
          class="secondary"
          :disabled="scale <= 0.5"
          @click="scale = Math.max(0.5, scale - 0.25)"
        >
          缩小
        </button>
        <button
          class="secondary"
          @click="rotation += 90"
        >
          旋转
        </button>
        <button
          class="text-button"
          @click="
            scale = 1;
            rotation = 0;
            offset = { x: 0, y: 0 };
          "
        >
          复位
        </button>
      </div>
      <div
        class="viewer-canvas"
        @pointerdown="start"
        @pointermove="pan"
        @pointerup="drag = null"
        @pointercancel="drag = null"
      >
        <p
          v-if="!sample.url || failed"
          class="inline-error"
        >
          此来源无法预览。链接可能过期或需要原站登录，可重新采集相关材料。
        </p>
        <img
          v-else
          :key="sample.sample_id"
          :src="sample.url"
          :alt="sample.label || '原始材料'"
          draggable="false"
          referrerpolicy="no-referrer"
          :style="{
            transform: `translate(${offset.x}px, ${offset.y}px) rotate(${rotation}deg) scale(${scale})`,
          }"
          @error="failed = true"
        />
      </div>
      <p class="muted small">
        {{ sample.section || sample.group || "分区未确认" }} ·
        图片来自采集时地址，可拖动查看；未保存原图。
      </p>
    </div>
  </ModalDialog>
</template>

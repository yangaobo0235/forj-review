<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from "vue";
const props = defineProps<{ title: string; locked?: boolean; returnFocus?: HTMLElement | null }>();
const emit = defineEmits<{ close: [] }>();
const root = ref<HTMLElement | null>(null);
let previous: Element | null = null;
onMounted(() => {
  previous = props.returnFocus ?? document.activeElement;
  (root.value?.querySelector<HTMLButtonElement>("button:not(:disabled)") ?? root.value)?.focus();
});
onBeforeUnmount(() => {
  if (previous instanceof HTMLElement) previous.focus();
});
function trap(e: KeyboardEvent) {
  if (e.key !== "Tab") return;
  const items = [
    ...(root.value?.querySelectorAll<HTMLElement>(
      "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary",
    ) ?? []),
  ].filter((el) => el.getClientRects().length);
  if (!items.length) {
    e.preventDefault();
    return;
  }
  if (e.shiftKey && document.activeElement === items[0]) {
    e.preventDefault();
    items.at(-1)?.focus();
  }
  if (!e.shiftKey && document.activeElement === items.at(-1)) {
    e.preventDefault();
    items[0]?.focus();
  }
}
</script>
<template>
  <div
    class="dialog-backdrop"
    @click.self="!locked && emit('close')"
  >
    <section
      ref="root"
      class="source-dialog"
      tabindex="-1"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
      @keydown="trap"
      @keydown.esc="!locked && emit('close')"
    >
      <header class="panel-heading">
        <h2>{{ title }}</h2>
        <button
          class="secondary"
          :disabled="locked"
          @click="emit('close')"
        >
          关闭
        </button>
      </header>
      <slot />
    </section>
  </div>
</template>

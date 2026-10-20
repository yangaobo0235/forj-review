<script setup lang="ts">
import { computed } from "vue";
import type { ReviewTask } from "../../../shared/contracts/review";
import { isFieldFirstTaskVisible } from "../model/review-tasks";
const props = defineProps<{ tasks: readonly ReviewTask[] }>();
const visibleTasks = computed(() => props.tasks.filter(isFieldFirstTaskVisible));
</script>

<template>
  <section v-if="visibleTasks.length" class="result-card review-task-list">
    <div class="group-heading"><h2>审核任务</h2><small>{{ visibleTasks.length }} 项</small></div>
    <article v-for="task in visibleTasks" :key="task.step_id" :class="`check-row status-${task.result_status.toLowerCase()}`">
      <span>{{ task.sequence }}. {{ task.label }}</span><strong>{{ task.result_status === "MATCH" ? "通过" : "待处理" }}</strong><p class="muted">{{ task.reason }}</p>
    </article>
  </section>
</template>

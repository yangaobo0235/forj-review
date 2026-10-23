<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { request } from "../api/client";
import type { Workbench } from "../model/use-workbench";
import ModalDialog from "../ui/ModalDialog.vue";

type Conflict = {
  path: string;
  base: unknown;
  candidate: unknown;
  current: unknown;
  candidate_missing: boolean;
  current_missing: boolean;
};
type Rebase = { baseline_digest: string; current_version: string; conflicts: Conflict[] };
const { work } = defineProps<{ work: Workbench }>();
const session = computed(() => work.session!);
const locked = computed(
  () =>
    Boolean(work.busy) ||
    work.running ||
    session.value.state === "CLOSED" ||
    Boolean(session.value.pending?.confirmed),
);
const rebase = ref<Rebase | null>(null),
  resolutions = ref<Record<string, "current" | "candidate">>({});
const chosen = ref<string[]>([]),
  reason = ref(""),
  independent = ref(false),
  version = ref("");
const requirements = computed(
  () => session.value.requirements?.filter((r) => (r.status ?? "ACTIVE") === "ACTIVE") ?? [],
);
const versions = computed(
  () => work.templates.find((p) => p.page_id === session.value.draft.page_id)?.versions ?? [],
);
const checks = computed(
  () => session.value.local_report?.evidence?.filter((e) => e.tool === "run_check") ?? [],
);
watch(
  () => [session.value.task_id, session.value.revision, session.value.intent_revision],
  () => {
    rebase.value = null;
    resolutions.value = {};
    chosen.value = [];
    independent.value = false;
  },
);
async function loadRebase() {
  await work.run("比较活动基线", async () => {
    const sid = session.value.task_id,
      revision = session.value.revision,
      intent = session.value.intent_revision;
    const value = await request<Rebase>(`/tasks/${sid}/rebase`);
    if (
      sid === work.sessionId &&
      revision === session.value.revision &&
      intent === session.value.intent_revision
    )
      rebase.value = value;
  });
}
async function importReport(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  await work.run("接续本地检查报告", async () => {
    if (file.size > 4 * 1024 * 1024)
      throw new Error("报告超过 4 MB，请先查看本地报告并精简无关输出");
    await work.importLocalReport(JSON.parse(await file.text()));
    work.setNotice(
      "已记录开发人员提供的本地报告。检查结论仅覆盖报告注明的范围，仍需人工评审与部署核对。",
    );
  });
  (event.target as HTMLInputElement).value = "";
}
function rollback() {
  const pageId = String(session.value.draft.page_id),
    parentSessionId = session.value.task_id;
  void work.run("准备历史版本恢复", () =>
    work.startTask(
      { pageId, message: "", rollbackVersion: version.value, parentSessionId },
      work.source,
    ),
  );
}
</script>

<template>
  <section
    v-if="session.local_report"
    class="surface"
    aria-label="本地交付结果"
  >
    <h3>
      {{
        session.task.local_report_current
          ? session.task.label
          : "历史本地报告：目标已变化，需重新调查"
      }}
    </h3>
    <p>{{ session.local_report.delivery?.summary }}</p>
    <p
      v-for="obstacle in session.local_report.delivery?.obstacles"
      :key="obstacle"
      class="blocker"
    >
      {{ obstacle }}
    </p>
    <p class="muted">{{ session.local_report.verification_scope }}</p>
    <p>本地报告由开发人员提供；已交付不表示需求已上线。</p>
    <details>
      <summary>改动文件与补丁摘要</summary>
      <p
        v-for="file in session.local_report.changed_files"
        :key="file"
      >
        {{ file }}
      </p>
      <code>{{ session.local_report.patch_digest }}</code>
      <p>changes.patch 保存在开发本地任务目录，由开发人员评审应用。</p>
    </details>
    <details>
      <summary>登记检查与实际调查依据</summary>
      <div
        v-for="check in checks"
        :key="check.id"
        class="result-row"
      >
        <span>{{ check.result.check_id }}</span
        ><strong>{{ check.result.status }}</strong>
      </div>
      <pre>{{ JSON.stringify(session.local_report.evidence, null, 2) }}</pre>
    </details>
  </section>
  <details
    v-if="session.route === 'LOCAL_CODE' && !session.receipt"
    class="surface"
  >
    <summary>开发人员：导入本地接续报告</summary>
    <p>
      选择本地任务目录中的 report.json。报告须匹配本次任务包和需求版本；导入不会应用补丁或发布配置。
    </p>
    <input
      type="file"
      accept="application/json,.json"
      aria-label="本地接续报告"
      :disabled="locked"
      @change="importReport"
    />
  </details>
  <details
    v-if="!session.receipt && session.state !== 'CLOSED'"
    class="surface"
  >
    <summary>更新基线与独立拆分</summary>
    <p>
      基线更新会三方比较已保存基线、本次候选与当前活动配置，冲突逐项确认。原任务保留，生成关联任务重新验证。
    </p>
    <button
      class="secondary"
      :disabled="locked"
      @click="loadRebase"
    >
      比较并更新基线
    </button>
    <template v-if="requirements.length > 1">
      <h3>仅接续明确独立的部分修改</h3>
      <p class="muted">
        原任务保留全部目标；新任务从当前活动配置开始，仍需独立验收，不会直接发布已有候选。
      </p>
      <label
        v-for="item in requirements"
        :key="item.requirement_id"
        ><span
          ><input
            v-model="chosen"
            type="checkbox"
            :value="item.requirement_id"
            :disabled="locked"
          />{{ item.text }}</span
        ></label
      >
      <label
        >为何能独立验收<textarea
          v-model="reason"
          :disabled="locked"
          rows="2"
        />
      </label>
      <label
        ><span
          ><input
            v-model="independent"
            type="checkbox"
            :disabled="locked"
          />我确认所选项能独立交付，同意其余项继续保留在原任务。</span
        ></label
      >
      <button
        class="secondary"
        :disabled="
          locked ||
          !independent ||
          !reason.trim() ||
          !chosen.length ||
          chosen.length === requirements.length
        "
        @click="
          work.run('创建独立修改任务', () =>
            work.fork('split', { requirement_ids: chosen, independence_reason: reason }),
          )
        "
      >
        创建关联子任务
      </button>
    </template>
  </details>
  <details
    v-if="versions.length"
    class="surface"
  >
    <summary>页面版本记录与恢复</summary>
    <p>历史版本保留原内容。恢复先创建独立任务，展示差异、验证并明确发布，再核对实际采用。</p>
    <label
      >已登记版本<select
        v-model="version"
        :disabled="locked"
      >
        <option value="">选择版本</option>
        <option
          v-for="item in versions"
          :key="item"
        >
          {{ item }}
        </option>
      </select></label
    >
    <button
      class="secondary"
      :disabled="locked || !version"
      @click="rollback"
    >
      预览恢复这个版本
    </button>
  </details>
  <p
    v-if="session.parent_task_id"
    class="muted small"
  >
    关联来源：<button
      class="text-button"
      :disabled="Boolean(work.busy)"
      @click="work.navigate(session.parent_task_id!)"
    >
      查看原任务
    </button>
  </p>
  <p
    v-for="child in session.child_tasks"
    :key="child.task_id"
    class="muted small"
  >
    {{ child.reason }}：<button
      class="text-button"
      :disabled="Boolean(work.busy)"
      @click="work.navigate(child.task_id)"
    >
      查看关联任务
    </button>
  </p>
  <ModalDialog
    v-if="rebase"
    title="核对基线更新"
    :locked="Boolean(work.busy)"
    @close="rebase = null"
  >
    <p>当前活动版本：{{ rebase.current_version }}。需求与非冲突修改保留，所有发布证明重新检查。</p>
    <p v-if="!rebase.conflicts.length">没有互相冲突的修改，可以合并并继续。</p>
    <article
      v-for="conflict in rebase.conflicts"
      :key="conflict.path"
      class="surface"
    >
      <h3>{{ conflict.path }}</h3>
      <details>
        <summary>原基线</summary>
        <pre>{{ JSON.stringify(conflict.base, null, 2) }}</pre>
      </details>
      <label
        >保留哪一侧<select
          v-model="resolutions[conflict.path]"
          :disabled="Boolean(work.busy)"
        >
          <option
            :value="undefined"
            disabled
          >
            请选择，没有默认答案
          </option>
          <option value="current">当前活动配置</option>
          <option value="candidate">本次候选</option>
        </select></label
      >
      <div class="before-after">
        <div>
          <strong>当前活动配置</strong>
          <pre>{{
            conflict.current_missing ? "已删除" : JSON.stringify(conflict.current, null, 2)
          }}</pre>
        </div>
        <div>
          <strong>本次候选</strong>
          <pre>{{
            conflict.candidate_missing ? "已删除" : JSON.stringify(conflict.candidate, null, 2)
          }}</pre>
        </div>
      </div>
    </article>
    <p
      v-if="work.error"
      class="inline-error"
      role="alert"
    >
      {{ work.error }}
    </p>
    <button
      class="primary"
      :disabled="Boolean(work.busy) || rebase.conflicts.some((c) => !resolutions[c.path])"
      @click="
        work.run('更新基线并保留目标', () =>
          work.fork('rebase', { baseline_digest: rebase!.baseline_digest, resolutions }),
        )
      "
    >
      确认并创建接续任务
    </button>
  </ModalDialog>
</template>

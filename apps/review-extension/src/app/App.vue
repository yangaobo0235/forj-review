<script setup lang="ts">
import { computed, ref, onScopeDispose } from "vue";
import { fetchPageCatalog, type PageCatalog } from "../platform/browser/catalog/page-catalog";
import { reviewApiBaseUrl } from "../features/review/api/review-client";
import forjLogo from "../assets/forj-logo.png";
import Icon from "../shared/ui/Icon.vue";
import ReviewProgress from "../features/review/ui/ReviewProgress.vue";
import MaterialPresence from "../features/review/ui/MaterialPresence.vue";
import ReviewResults from "../features/review/ui/ReviewResults.vue";
import { useReviewWorkflow } from "../features/review/model/use-review-workflow";
import {
  businessLabels,
  catalogBusinessChoices,
  catalogBusinessLabel,
  type BusinessChoice,
} from "../features/review/model/review-panel";
import type { PageData } from "../shared/contracts/review";
import CaptureLauncher from "../features/configuration-capture/CaptureLauncher.vue";
import "./App.css";

const businessSelection = ref<BusinessChoice>("AUTO");
const pageCatalog = ref<PageCatalog | null>(null);
const workflow = useReviewWorkflow(businessSelection);
const materialPresence = computed(
  () =>
    workflow.job?.material_completeness?.presence ??
    workflow.review?.material_completeness?.presence,
);
let live = true;
onScopeDispose(() => {
  live = false;
});
void fetchPageCatalog(reviewApiBaseUrl).then((catalog) => {
  if (live) pageCatalog.value = catalog;
});

function currentBusinessLabel(page: PageData): string {
  const identity = pageCatalog.value?.identities.find(
    (item) =>
      item.business_type === page.businessType &&
      item.region === page.region &&
      (!page.pageId || item.page_id === page.pageId),
  );
  if (identity) return catalogBusinessLabel(identity);
  const key = `${page.businessType}_${page.region}` as BusinessChoice;
  return (
    businessLabels[key] ??
    businessLabels[page.businessType as BusinessChoice] ??
    String(page.businessType)
  );
}
function changeBusinessSelection(event: Event) {
  workflow.reset();
  businessSelection.value = (event.target as HTMLSelectElement).value as BusinessChoice;
}
async function refreshPage() {
  const catalog = await fetchPageCatalog(reviewApiBaseUrl);
  if (live) pageCatalog.value = catalog;
  await workflow.refreshPage();
}
</script>

<template>
  <main class="review-panel">
    <header class="panel-header">
      <div class="panel-header-inner">
        <div class="brand-header">
          <img
            class="brand-logo"
            :src="forjLogo"
            alt="赋界科技"
          />
          <div class="brand-copy">
            <h1>审核辅助工作台</h1>
            <p>材料识别与字段核对</p>
          </div>
        </div>
        <details class="panel-more">
          <summary>更多</summary>
          <div class="panel-more-content"><CaptureLauncher /></div>
        </details>
      </div>
      <section
        class="review-context"
        aria-label="当前审核页面"
      >
        <div class="context-heading">
          <Icon
            name="file"
            :size="14"
          /><span>当前页面</span>
          <h2 :title="workflow.pageData?.pageTitle">
            {{ workflow.pageData?.pageTitle || "等待读取审核页面" }}
          </h2>
          <span class="context-mode">{{
            workflow.pageData?.businessType ? "已识别" : "待识别"
          }}</span>
        </div>
        <p class="context-business">
          {{
            workflow.pageData?.businessType
              ? currentBusinessLabel(workflow.pageData)
              : "在业务页面打开助手，读取字段和材料后开始审核。"
          }}<span v-if="workflow.pageData?.businessType">
            · {{ workflow.pageData.selectionMode === "MANUAL" ? "手动选择" : "自动匹配" }}</span
          >
        </p>
        <div class="review-toolbar">
          <section
            class="business-control"
            aria-label="审核业务选择"
          >
            <label for="business-selection">审核业务</label>
            <select
              id="business-selection"
              :value="businessSelection"
              :disabled="workflow.loading"
              title="自动匹配页面规则，也可手动选择审核业务"
              @change="changeBusinessSelection"
            >
              <option
                v-for="(label, value) in catalogBusinessChoices(pageCatalog)"
                :key="value"
                :value="value"
              >
                {{ label }}
              </option>
            </select>
          </section>
          <div class="review-toolbar-actions">
            <button
              class="refresh-page-button"
              :disabled="workflow.loading"
              @click="refreshPage"
            >
              重新读取
            </button>
            <button
              class="start-review-button"
              :aria-label="workflow.loading ? '正在审核' : '开始审核检查'"
              :disabled="
                workflow.loading ||
                !workflow.pageData?.businessType ||
                Boolean(workflow.pageData?.reviewServiceError)
              "
              @click="workflow.startReview"
            >
              <Icon
                :name="workflow.loading ? 'clock' : 'fileCheck'"
                :size="16"
              />
              {{ workflow.loading ? "正在审核…" : "开始审核" }}
            </button>
          </div>
        </div>
      </section>
    </header>
    <div class="panel-content">
      <section
        v-if="!workflow.loading && !workflow.review && !workflow.error"
        class="review-empty"
        aria-label="审核说明"
      >
        <span class="empty-symbol"
          ><Icon
            name="shield"
            :size="24"
        /></span>
        <h2>从资料到结论，逐项核对</h2>
        <p>识别页面材料，核对字段差异。<br />需要人工判断的项目会集中展示。</p>
        <div class="review-outline">
          <span>读取材料</span
          ><Icon
            name="chevron"
            :size="12"
          /><span>核对字段</span
          ><Icon
            name="chevron"
            :size="12"
          /><span>人工复核</span>
        </div>
      </section>
      <div
        v-if="workflow.loading && workflow.stageMessage"
        class="notice-message"
        role="status"
      >
        {{ workflow.stageMessage }}
      </div>
      <div
        v-if="workflow.error"
        class="error-message"
        role="alert"
      >
        {{ workflow.error }}
      </div>
      <div
        v-if="workflow.notice"
        class="notice-message"
        role="status"
      >
        {{ workflow.notice }}
      </div>
      <section
        v-if="workflow.job || materialPresence"
        class="review-overview"
        aria-label="识别进度与材料完整性"
      >
        <ReviewProgress
          v-if="workflow.job"
          :job="workflow.job"
          :page-data="workflow.pageData"
          :on-focus-image="workflow.focusOriginalImage"
        />
        <MaterialPresence
          v-if="materialPresence"
          :key="workflow.job?.job_id"
          :report="materialPresence"
          :on-focus-image="workflow.focusOriginalImage"
        />
      </section>
      <ReviewResults
        v-if="workflow.review"
        :review="workflow.review"
        :page-data="workflow.pageData"
        :on-focus-image="workflow.focusOriginalImage"
        :on-apply-page-field-value="workflow.applyPageFieldValue"
        :on-apply-page-field-group-value="workflow.applyPageFieldGroupValue"
        :on-apply-vehicle-model="workflow.applyVehicleModel"
        :on-rerun="workflow.startReview"
      />
    </div>
    <footer class="panel-footer">
      <Icon
        name="shield"
        :size="13"
      /><span>辅助人工复核，不会自动提交审核决定</span>
    </footer>
  </main>
</template>

<script setup lang="ts">
import { ref, onScopeDispose } from "vue";
import { fetchPageCatalog, type PageCatalog } from "../platform/browser/catalog/page-catalog";
import { agentBaseUrl } from "../features/review/api/review-client";
import forjLogo from "../assets/forj-logo.png";
import Icon from "../shared/ui/Icon.vue";
import ReviewProgress from "../features/review/ui/ReviewProgress.vue";
import ReviewResults from "../features/review/ui/ReviewResults.vue";
import type { ExceptionFilter } from "../features/review/presentation/exception";
import { useReviewWorkflow } from "../features/review/model/use-review-workflow";
import { businessLabels, catalogBusinessChoices, catalogBusinessLabel, type BusinessChoice } from "../features/review/model/review-panel";
import type { PageData } from "../shared/contracts/review";
import CaptureLauncher from "../features/configuration-capture/CaptureLauncher.vue";
import "./App.css";

const businessSelection = ref<BusinessChoice>("AUTO");
const exceptionFilter = ref<ExceptionFilter>("ALL");
const pageCatalog = ref<PageCatalog | null>(null);
const workflow = useReviewWorkflow(businessSelection);
let live = true;
onScopeDispose(() => { live = false; });
void fetchPageCatalog(agentBaseUrl).then(catalog => { if (live) pageCatalog.value = catalog; });

function currentBusinessLabel(page: PageData): string {
  const identity = pageCatalog.value?.identities.find(item => item.business_type === page.businessType
    && item.region === page.region && (!page.pageId || item.page_id === page.pageId));
  if (identity) return catalogBusinessLabel(identity);
  const key = `${page.businessType}_${page.region}` as BusinessChoice;
  return businessLabels[key] ?? businessLabels[page.businessType as BusinessChoice] ?? String(page.businessType);
}
function changeBusinessSelection(event: Event) {
  workflow.reset();
  businessSelection.value = (event.target as HTMLSelectElement).value as BusinessChoice;
}
</script>

<template>
  <main class="review-panel">
    <header class="panel-header">
      <div class="brand-header"><img class="brand-logo" :src="forjLogo" alt="赋界科技"/><div class="brand-copy"><h1>资料审核工作台</h1><p>材料识别与字段核对</p></div></div>
      <CaptureLauncher/>
    </header>
    <div class="panel-content">
      <section class="review-context" aria-label="当前审核页面">
        <div class="context-heading"><Icon name="file" :size="14"/><span>当前页面</span><h2 :title="workflow.pageData?.pageTitle">{{ workflow.pageData?.pageTitle || "等待读取审核页面" }}</h2><span class="context-mode">{{ workflow.pageData?.businessType ? "已识别" : "待识别" }}</span></div>
        <p class="context-business">{{ workflow.pageData?.businessType ? currentBusinessLabel(workflow.pageData) : "在业务页面打开助手，读取字段和材料后开始审核。" }}<span v-if="workflow.pageData?.businessType"> · {{ workflow.pageData.selectionMode === "MANUAL" ? "手动选择" : "自动匹配" }}</span></p>
        <div class="review-toolbar">
          <section class="business-control" aria-label="审核业务选择">
            <label for="business-selection">业务识别</label>
            <select id="business-selection" :value="businessSelection" :disabled="workflow.loading" title="自动匹配页面规则，也可手动选择审核业务" @change="changeBusinessSelection">
              <option v-for="(label, value) in catalogBusinessChoices(pageCatalog)" :key="value" :value="value">{{ label }}</option>
            </select>
          </section>
          <button class="start-review-button" :aria-label="workflow.loading ? '正在审核' : '开始审核检查'" :disabled="workflow.loading || !workflow.pageData?.businessType" @click="workflow.startReview">
            <Icon :name="workflow.loading ? 'clock' : 'fileCheck'" :size="16"/>
            {{ workflow.loading ? "正在审核…" : "开始审核" }}
          </button>
        </div>
      </section>
      <section v-if="!workflow.loading && !workflow.review && !workflow.error" class="review-empty" aria-label="审核说明">
        <span class="empty-symbol"><Icon name="shield" :size="24"/></span><h2>从资料到结论，逐项核对</h2>
        <p>识别页面材料，核对字段差异。<br/>需要人工判断的项目会集中展示。</p>
        <div class="review-outline"><span>读取材料</span><Icon name="chevron" :size="12"/><span>核对字段</span><Icon name="chevron" :size="12"/><span>人工复核</span></div>
      </section>
      <div v-if="workflow.loading && workflow.stageMessage" class="notice-message" role="status">{{ workflow.stageMessage }}</div>
      <div v-if="workflow.error" class="error-message" role="alert">{{ workflow.error }}</div>
      <div v-if="workflow.notice" class="notice-message" role="status">{{ workflow.notice }}</div>
      <ReviewProgress v-if="workflow.job" :job="workflow.job" :page-data="workflow.pageData" :on-focus-image="workflow.focusOriginalImage"/>
      <ReviewResults v-if="workflow.review" :review="workflow.review" :job="workflow.job" :page-data="workflow.pageData"
        :exception-filter="exceptionFilter" :on-exception-filter-change="value => exceptionFilter = value"
        :on-focus-image="workflow.focusOriginalImage" :on-apply-page-field-value="workflow.applyPageFieldValue"
        :on-apply-page-field-group-value="workflow.applyPageFieldGroupValue" :on-rerun="workflow.startReview"/>
    </div>
    <footer class="panel-footer"><Icon name="shield" :size="13"/><span>辅助人工复核，不会自动提交审核决定</span></footer>
  </main>
</template>

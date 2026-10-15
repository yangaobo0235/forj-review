/**
 * 功能：组合 Side Panel 页面并管理业务与异常筛选。
 * 职责边界：不管理任务轮询，不实现后端审核规则；
 * 报废置换字段审核由结果工作台在侧边栏内管理。
 * 修改日期：2026-09-11
 * 修改人：wuyi
 */

import { useEffect, useState } from "react";
import { fetchPageCatalog } from "../platform/browser/catalog/page-catalog";
import type { PageCatalog } from "../platform/browser/catalog/page-catalog";
import { agentBaseUrl } from "../features/review/api/review-client";

import "./App.css";
import forjLogo from "../assets/forj-logo.png";
import { ReviewProgress } from "../features/review/ui/ReviewProgress";
import { ReviewResults } from "../features/review/ui/ReviewResults";
import type { ExceptionFilter } from "../features/review/presentation/exception";
import { useReviewWorkflow } from "../features/review/model/use-review-workflow";
import { businessLabels, catalogBusinessChoices, catalogBusinessLabel, type BusinessChoice } from "../features/review/model/review-panel";
import type { PageData } from "../shared/contracts/review";

/**
 * 当前识别出来的业务名称。
 *
 * 按「业务_地区」拼键查下拉里的同一份标签，不在这里按业务写 switch——
 * 每加一个共用地址的业务就要再补一个分支，漏一个不会报错，只会显示成
 * 字段键。
 */
function currentBusinessLabel(page: PageData, catalog: PageCatalog | null): string {
  const identity = catalog?.identities.find(item => item.business_type === page.businessType
    && item.region === page.region && (!page.pageId || item.page_id === page.pageId));
  if (identity) return catalogBusinessLabel(identity);
  const key = `${page.businessType}_${page.region}` as BusinessChoice;
  return businessLabels[key] ?? businessLabels[page.businessType as BusinessChoice] ?? String(page.businessType);
}

function App() {
  const [businessSelection, setBusinessSelection] = useState<BusinessChoice>("AUTO");
  const [exceptionFilter, setExceptionFilter] = useState<ExceptionFilter>("ALL");
  const workflow = useReviewWorkflow(businessSelection);
  const [pageCatalog, setPageCatalog] = useState<PageCatalog | null>(null);
  useEffect(() => {
    let live = true;
    void fetchPageCatalog(agentBaseUrl).then(catalog => {
      if (!live) return;
      setPageCatalog(catalog);
    });
    return () => { live = false; };
  }, []);

  const changeBusinessSelection = (selection: BusinessChoice) => {
    workflow.reset();
    setBusinessSelection(selection);
  };

  return (
    <main className="review-panel">
      <header>
        <div className="brand-header">
          <img className="brand-logo" src={forjLogo} alt="赋界科技" />
          <div className="brand-copy">
            <h1>车辆智能审核</h1>
            <p>识别资料差异，辅助人工复核</p>
          </div>
        </div>
      </header>

      <section className="business-control" aria-label="审核业务选择">
        <label htmlFor="business-selection">审核业务</label>
        <select
          id="business-selection"
          value={businessSelection}
          onChange={(event) => changeBusinessSelection(event.target.value as BusinessChoice)}
          disabled={workflow.loading}
        >
          {Object.entries(catalogBusinessChoices(pageCatalog)).map(([value, label]) => (
            <option value={value} key={value}>{label}</option>
          ))}
        </select>
        {workflow.pageData?.businessType ? (
          <small>
            当前：{currentBusinessLabel(workflow.pageData, pageCatalog)} ·{" "}
            {workflow.pageData.selectionMode === "MANUAL" ? "人工选择" : "自动识别"}
          </small>
        ) : null}
      </section>

      <button
        onClick={workflow.startReview}
        disabled={workflow.loading || !workflow.pageData?.businessType}
      >
        {workflow.loading ? "正在识别并核验……" : "开始审核检查"}
      </button>

      {workflow.loading && workflow.stageMessage ? (
        <div className="notice-message" role="status">{workflow.stageMessage}</div>
      ) : null}

      {workflow.error ? <div className="error-message">{workflow.error}</div> : null}
      {workflow.notice ? <div className="notice-message">{workflow.notice}</div> : null}
      {workflow.job ? <ReviewProgress job={workflow.job} pageData={workflow.pageData} onFocusImage={workflow.focusOriginalImage} /> : null}
      {workflow.review ? (
        <ReviewResults
          review={workflow.review}
          job={workflow.job}
          pageData={workflow.pageData}
          exceptionFilter={exceptionFilter}
          onExceptionFilterChange={setExceptionFilter}
          onFocusImage={workflow.focusOriginalImage}
          onApplyPageFieldValue={workflow.applyPageFieldValue}
          onApplyPageFieldGroupValue={workflow.applyPageFieldGroupValue}
          onRerun={workflow.startReview}
        />
      ) : null}

      <footer>所有审核结果仅供人工参考，不会自动提交审核决定</footer>
    </main>
  );
}

export default App;

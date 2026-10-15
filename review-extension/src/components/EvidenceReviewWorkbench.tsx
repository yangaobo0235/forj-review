import { useCallback, useMemo, useState } from "react";

import { evidencePresentation } from "../evidencePresentation";
import { fieldLabel } from "../reviewPanelConfig";
import { isBlockingPageFillFailure, type PageFillResult } from "../pageFillResult";
import { isFieldFirstTaskVisible, isQrTask } from "../reviewSteps";
import type { CheckResultValue, EvidenceFact, PageData, PageImage, QrCheck, ReviewResponse, ReviewTask } from "../types/review";

/**
 * 字段优先工作台：按后端给出的字段条目展示证据、结论和人工处置。
 *
 * 显示策略由后端业务声明投影，
 * 组件只解释证据、任务与操作，不依赖业务 ID 或配置版本。
 */
interface Props {
  readOnly?: boolean;
  review: ReviewResponse;
  pageData?: PageData | null;
  onFocusImage?: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
  onApplyPageFieldValue?: (field: string, value: string, expectedValue?: string | null) => Promise<PageFillResult>;
  onApplyPageFieldGroupValue?: (fields: string[], value: string, expectedValues?: Record<string, string | null | undefined>) => Promise<PageFillResult>;
  onRerun?: () => Promise<void>;
}

type View = "PENDING" | "ALL" | "EXTERNAL";
type Decision = "MARKED_EXCEPTION";
const statusText = { MATCH: "一致", CONFLICT: "冲突", INSUFFICIENT: "待复核" } as const;


export function EvidenceReviewWorkbench(props: Props) {
  return <Workbench key={props.pageData?.collectionId} {...props} />;
}

function Workbench({ review, pageData, readOnly = false, onFocusImage = async () => ({ ok: false, error: "原图定位未配置" }), onApplyPageFieldValue = async () => ({ ok: false, message: "页面写回未配置" }), onApplyPageFieldGroupValue = async () => ({ ok: false, message: "页面组合字段写回未配置" }), onRerun }: Props & { review: ReviewResponse }) {
  const steps = useMemo(() => [...(review.review_tasks ?? [])].sort((a, b) => a.sequence - b.sequence), [review.review_tasks]);
  // Apply the presentation policy before deriving tabs, counts or selection,
  // so hidden backend compatibility tasks cannot leak into a secondary view.
  // 日期类规则结论已经由后端并进对应字段行（合并关系来自业务声明的
  // `field_check_bindings`），这里不再自己拿一张表去猜该藏哪条任务——
  // 前端持有那张表时，新增一个带日期规则的业务就要回来补一行。
  const displaySteps = steps.filter(isFieldFirstTaskVisible);
  const [view, setView] = useState<View>("PENDING");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [message, setMessage] = useState("");
  const [filledCount, setFilledCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [blockingIssue, setBlockingIssue] = useState("");
  const effectiveNeedsAction = (step: ReviewTask) => step.requires_reviewer_action;
  const pending = displaySteps.filter((step) => effectiveNeedsAction(step) && !decisions[step.step_id]);
  const visible = view === "PENDING"
    ? pending
    : view === "EXTERNAL"
      ? displaySteps.filter(isExternalTask)
      : displaySteps.filter((step) => step.category === "FIELD");
  const current = visible.find((step) => step.step_id === selectedId) ?? visible[0] ?? null;
  const choose = (step: ReviewTask, decision: Decision) => { setDecisions((previous) => ({ ...previous, [step.step_id]: decision })); setSelectedId(null); setMessage("已记录本项人工处置"); };
  const handleWriteFailure = useCallback((reason: unknown) => {
    const failure = typeof reason === "object" && reason && "message" in reason
      ? reason as Pick<PageFillResult, "message" | "code">
      : { message: typeof reason === "string" ? reason : "页面回填失败" };
    setMessage(failure.message);
    if (isBlockingPageFillFailure(failure)) setBlockingIssue(failure.message);
  }, []);
  // 原图定位的结果就地显示：面板顶部的提示离字段卡片很远，只在那里报错
  // 等于"点了没反应"。
  const focus = async (imageId: string) => {
    setMessage("正在定位原图…");
    const result = await onFocusImage(imageId);
    setMessage(result?.ok
      ? "已定位到原图，页面已滚动并高亮该图；如未看到请检查原审核页面"
      : result?.error || "原图定位失败，请重新采集");
  };
  const fill = async (step: ReviewTask, value: string) => { const field = fieldFromStep(step); if (!field || !pageData || busy || blockingIssue) return; const fields = step.page_target_fields?.length ? step.page_target_fields : [field]; const expectedValues = Object.fromEntries(fields.map((targetField) => [targetField, pageData.pageFields[targetField] ?? null])); setBusy(true); setMessage(fields.length > 1 ? "正在联合回填并回读页面字段..." : "正在回填并回读页面字段..."); try { const result = fields.length > 1 ? await onApplyPageFieldGroupValue(fields, value, expectedValues) : await onApplyPageFieldValue(field, value, expectedValues[field]); if (result.ok) { setFilledCount((count) => count + 1); setMessage(`${stepTitle(step)}已${fields.length > 1 ? "同时回填两个字段并" : ""}回读，已定位到页面字段；核对后请点击“标记人工复核”`); } else handleWriteFailure(result); } catch (error) { handleWriteFailure(error); } finally { setBusy(false); } };
  const allDone = pending.length === 0;
  return (
    <section className="review-workbench" aria-label="字段证据审核工作台">
      <header className="workbench-summary">
        <div><strong>字段证据审核</strong><small>自动通过 {displaySteps.filter((step) => !effectiveNeedsAction(step)).length} · 已人工处理 {Object.keys(decisions).length} · 待处理 {pending.length}</small></div>
        <b>{blockingIssue ? "页面已失效" : allDone ? "展示项目已处理" : `${pending.length} 项待处理`}</b>
      </header>
      {blockingIssue ? <div className="workbench-blocked" role="alert">
        <strong>页面操作已停止</strong><span>{blockingIssue}</span>
        {onRerun ? <button type="button" onClick={() => void onRerun()}>重新采集并复核</button> : null}
      </div> : null}
      <nav className="workbench-tabs" aria-label="审核视图">
        {workbenchViews({ pending: pending.length,
          fieldCount: displaySteps.filter((step) => step.category === "FIELD").length,
          externalCount: displaySteps.filter(isExternalTask).length,
        }).map(([key, label]) => <button type="button" aria-pressed={view === key}
          className={view === key ? "active" : ""} key={key}
          onClick={() => { setView(key); setSelectedId(null); setMessage(""); }}>{label}</button>)}
      </nav>
      <div className="workbench-content">
        <nav className="workbench-index" aria-label="核验项目">
          {visible.map((step) => <button type="button" aria-pressed={current?.step_id === step.step_id}
            className={current?.step_id === step.step_id ? "selected" : ""} key={step.step_id}
            onClick={() => { setSelectedId(step.step_id); setMessage(""); }}>
            <span>{stepTitle(step)}</span><b className={`status-${step.result_status.toLowerCase()}`}>
              {decisions[step.step_id] ? "已处理" : statusText[step.result_status]}
            </b>
          </button>)}
        </nav>
        <div className="workbench-detail">
          {current ? <StepDetail step={current} pageData={pageData} qrChecks={review.qr_checks}
            disabled={readOnly || busy || Boolean(blockingIssue)} onFocusImage={focus} onFill={fill} onChoose={choose} />
            : <div className="workbench-empty"><strong>{allDone ? "当前展示项目已处理" : "当前没有待处理项目"}</strong><p>可切换到全部字段查看完整核验记录。</p></div>}
          {message ? <p className="workbench-message" role="status">{message}</p> : null}
        </div>
      </div>
      {filledCount > 0 && onRerun && !blockingIssue ? <div className="final-review-action">
        <span>页面已发生 {filledCount} 项回填</span>
        <button type="button" onClick={() => void onRerun()}>重新采集并最终复核</button>
      </div> : null}
    </section>
  );
}

type ViewSpec = readonly [View, string];

/**
 * 工作台视图表。
 *
 * “页面外核验”放 EXTERNAL 任务（例如二维码官网核验）和未投影的独立
 * BUSINESS_RULE 结论。三个视图始终存在，数量来自实际可见任务。
 */
function workbenchViews({ pending, fieldCount, externalCount }: {
  pending: number;
  fieldCount: number;
  externalCount: number;
}): readonly ViewSpec[] {
  return [["PENDING", `待处理 (${pending})`], ["ALL", `全部字段 (${fieldCount})`], ["EXTERNAL", `页面外核验 (${externalCount})`]];
}

function fieldFromStep(step: ReviewTask) { return step.page_field ?? step.page_target_field ?? null; }
function isExternalTask(step: ReviewTask) { return step.category === "EXTERNAL" || step.category === "BUSINESS_RULE"; }
function stepTitle(step: ReviewTask) { return step.label; }
function StepDetail({ step, pageData, qrChecks, disabled, onFocusImage, onFill, onChoose }: { step: ReviewTask; pageData?: PageData | null; qrChecks: QrCheck[]; disabled: boolean; onFocusImage: (id: string) => Promise<void>; onFill: (step: ReviewTask, value: string) => Promise<void>; onChoose: (step: ReviewTask, decision: Decision) => void }) {
  const field = fieldFromStep(step);
  const pageValue = field ? pageData?.pageFields[field] ?? step.page_value : step.page_value;
  const values = isQrTask(step) ? [] : step.values.filter((item) => item.source !== "申请页面字段" && item.value != null && String(item.value).trim() && String(item.value) !== "[object Object]");
  const images = new Map((pageData?.images ?? []).filter((image) => image.imageId).map((image) => [image.imageId as string, image]));
  const effectiveStatus = step.result_status;
  const requiresAction = step.requires_reviewer_action;
  const pageValues = step.page_values?.length ? step.page_values : [{ source: "页面原始值", value: pageValue }];
  // 页面字段条目一定有页面原值；规则结论**只有自己带了页面侧取值时**才摆这一块
  // （新旧车所有人一致性要先让审核员看到页面比出了什么）。没有的一律不显示，
  // 否则会凭空渲染一行“未采集”。
  const hasPageValues = step.category === "FIELD" || Boolean(step.page_values?.length);
  return <article className="workbench-card">
    <div className="workbench-card-title"><div><small>{CATEGORY_LABELS[step.category] ?? "核验"}</small><h2>{stepTitle(step)}</h2></div><b className={`status-${effectiveStatus.toLowerCase()}`}>{statusText[effectiveStatus]}</b></div>
    {hasPageValues ? <div className="page-value"><span>页面原始值</span>{pageValues.map((item) => <div className="page-value-row" key={item.source}>{pageValueLabel(item.source) !== "页面原始值" ? <small>{pageValueLabel(item.source)}</small> : null}<strong>{cleanPageValue(item.value) || "未采集"}</strong></div>)}{step.details?.page_value_note ? <p className="page-value-note">{step.details.page_value_note}</p> : null}</div> : null}
    {values.length ? <div className="candidate-list"><span className="evidence-section-title">材料提取值</span>{values.map((item, index) => { const image = (item.image_id ? images.get(item.image_id) : undefined) || (pageData?.images ?? []).find((candidate) => item.image_index != null && candidate.index === item.image_index); return <CandidateValue key={`${item.source}-${index}`} item={item} image={image} stepLabel={stepTitle(step)} canFill={Boolean(field) && step.result_status !== "MATCH"} disabled={disabled || !step.writable} onFocusImage={onFocusImage} onFill={(value) => onFill(step, value)} />; })}</div> : null}
    {step.details?.reason_distributed ? null : <p className="workbench-reason">{step.reason}</p>}
    {field ? <ManualValueInput key={`${pageData?.collectionId}-${field}`} initialValue={cleanPageValue(pageValue)} label={stepTitle(step)} disabled={disabled || !pageData || !step.writable} onFill={(value) => onFill(step, value)} /> : null}
    {isQrTask(step) ? qrChecks.map((check, index) => <QrUrl check={check} key={`${check.image_index}-${index}`} />) : null}
    <StructuredEvidence evidence={step.evidence} />
    {requiresAction ? <div className="workbench-actions"><button type="button" disabled={disabled} className="secondary-action" onClick={() => onChoose(step, "MARKED_EXCEPTION")}>标记人工复核</button></div> : null}
  </article>;
}
/**
 * 一条材料候选值：结论、与页面的比对结果、比对说明和原图都在同一个框里。
 *
 * 车型一个字段下挂着马力、整车型号、排放标准好几条结论，写着它们的其实是
 * 字段底部那段拼起来的长理由；删掉之后审核员得在框和理由之间来回对照。
 * 后端把每条检查自己的说明放在 `check_reason` 上，由本组件贴到框里。
 * `conflicting` 与 `check_reason` 都由后端给出，前端只负责显示，不自己比较取值。
 */
function CandidateValue({ item, image, stepLabel, canFill, disabled, onFocusImage, onFill }: {
  item: CheckResultValue;
  image?: PageImage;
  stepLabel: string;
  canFill: boolean;
  disabled: boolean;
  onFocusImage: (id: string) => Promise<void>;
  onFill: (value: string) => Promise<void>;
}) {
  const label = item.source === "图片识别" ? evidencePresentation(item).label : item.source;
  const comparison = item.conflicting == null ? "" : item.conflicting ? "与页面不一致" : "与页面一致";
  return <div className="candidate">
    <div>
      <strong>{renderDiff(item.value, item.differences)}</strong>
      <small>{label}{item.group_title ? ` · ${item.group_title}` : ""}{comparison ? <>{` · `}{markMismatch(comparison)}</> : null}{item.derived_from === "invoice.invoice_no" ? " · 由发票数电号码适配" : ""}</small>
      {item.uncertain ? <span className="candidate-uncertainty">识别不确定，请核对原图</span> : null}
      {item.check_reason ? <span className="candidate-basis">{markMismatch(item.check_reason)}</span> : null}
      {image ? <div className="candidate-evidence"><img src={image.src} alt="材料" />{image.imageId ? <button type="button" onClick={() => void onFocusImage(image.imageId as string)}>查看原图</button> : null}</div> : null}
    </div>
    {canFill ? <button aria-label={`${stepLabel}回填材料值`} className="fill-value-button" type="button" disabled={disabled} onClick={() => void onFill(String(item.value))}>回填此值</button> : null}
  </div>;
}

function ManualValueInput({ initialValue, label, disabled, onFill }: {
  initialValue: string;
  label: string;
  disabled: boolean;
  onFill: (value: string) => Promise<void>;
}) {
  const [value, setValue] = useState(initialValue);
  return <div className="manual-fill">
    <label>
      <span>人工输入值</span>
      <input aria-label={`${label}人工输入值`} value={value} disabled={disabled}
        onChange={(event) => setValue(event.target.value)} />
    </label>
    <button type="button" className="fill-value-button" disabled={disabled || !value.trim()}
      onClick={() => void onFill(value.trim())}>回填此值</button>
  </div>;
}

const CATEGORY_LABELS: Record<string, string> = {
  FIELD: "页面字段",
  BUSINESS_RULE: "业务规则",
  EXTERNAL: "页面外核验",
};

/**
 * 把说明里的「不一致」标红。
 *
 * 后端给的是完整句子，前端只做呈现：不改字、不加字、不据此改变任何状态。
 * 这里只认「不一致」三个字，与页面一致时不标，避免整句都被染红。
 */
const MISMATCH_TEXT = "不一致";
function markMismatch(text: string) {
  return text.split(MISMATCH_TEXT).flatMap((part, index) =>
    index === 0
      ? [part]
      : [<mark className="mismatch" key={index}>{MISMATCH_TEXT}</mark>, part],
  );
}

function cleanPageValue(value: unknown) { return String(value ?? "").trim(); }
function pageValueLabel(source: string) {
  return fieldLabel(source);
}
function renderDiff(value: unknown, differences: import("../types/review").DifferenceRange[] = []) {
  const chars = [...String(value ?? "")];
  return [...chars, ""].map((char, index) => <span key={index}>
    {differences.filter((range) => range.kind === "MISSING" && range.start === index).map((range, i) => <mark className="value-diff" key={i} title={`缺少页面字符：${range.page_text}`}>[缺少 {range.page_text}]</mark>)}
    {differences.some((range) => range.start <= index && index < range.end) ? <mark className="value-diff">{char}</mark> : char}
  </span>);
}
function QrUrl({ check }: { check: QrCheck }) {
  const verified = Boolean(check.url && check.domain_valid);
  return <>
    <div className="qr-verified-url">
      <span>识别官网网址</span>
      {verified ? <a href={check.url as string} target="_blank" rel="noreferrer noopener">{check.url}</a> : <strong>未取得已确认的官网网址</strong>}
      {verified && !check.accessible ? <small>官网地址已确认，后台暂未访问成功。可点击网址人工核验。</small> : null}
      {check.message ? <small className="qr-diagnostic">{check.message}</small> : null}
    </div>
    {check.raw_value ? <details className="qr-technical-details">
      <summary>查看技术详情</summary>
      {check.decoder ? <span>二维码来源：{check.decoder === "model" ? "模型" : check.decoder === "zxing" ? "ZXing" : check.decoder === "opencv" ? "OpenCV" : "未解码"}</span> : null}
      {check.decode_error ? <span>解码结果：{check.decode_error}{check.decode_elapsed_ms != null ? `（${check.decode_elapsed_ms} ms）` : ""}</span> : null}
      <span>原始二维码内容</span>
      <p>{verified ? <a href={check.url as string} target="_blank" rel="noreferrer noopener">{check.raw_value}</a> : check.raw_value}</p>
    </details> : null}
  </>;
}

function StructuredEvidence({ evidence }: { evidence: EvidenceFact[] }) { const rows = evidence.flatMap((item) => item.value && typeof item.value === "object" && !Array.isArray(item.value) ? Object.entries(item.value as Record<string, unknown>).filter(([, value]) => value != null && value !== "").map(([key, value]) => ({ source: item.source, key, value: String(value) })) : item.field?.startsWith("business_license.") && item.value != null && item.value !== "" ? [{ source: item.source, key: item.field, value: String(item.value) }] : []); return rows.length ? <dl className="structured-evidence">{rows.map((row, index) => <div key={`${row.source}-${row.key}-${index}`}><dt>{row.source} · {structuredFieldLabel(row.key)}</dt><dd>{row.value}</dd></div>)}</dl> : null; }
function structuredFieldLabel(field: string) { return ({ company_name: "企业名称", "business_license.company_name": "企业名称", legal_representative: "法定代表人", "business_license.legal_representative": "法定代表人", unified_social_credit_code: "统一社会信用代码", "business_license.unified_social_credit_code": "统一社会信用代码", vin: "车架号", certificate_no: "证明编号" } as Record<string, string>)[field] ?? field; }

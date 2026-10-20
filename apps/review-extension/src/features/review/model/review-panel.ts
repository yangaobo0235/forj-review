/**
 * 功能：集中维护业务、字段和状态展示标签。
 * 职责边界：不包含审核判定逻辑。
 * 修改日期：2026-08-26
 */

import { manifestFieldLabels } from "../../../platform/browser/catalog/collect-manifest.ts";
import { bundledPageCatalog } from "../../../platform/browser/catalog/bundled-page-catalog.ts";
import type { PageCatalog, PageIdentityDeclaration } from "../../../platform/browser/catalog/page-catalog.ts";
import type {
  BusinessSelection,
  BusinessType,
  FieldComparison,
  JobStatus,
} from "../../../shared/contracts/review";

export type BusinessChoice =
  | "AUTO"
  | "scrap_replacement_qingdao"
  | "scrap_replacement_changchun"
  | "vehicle_source"
  | "transfer"
  | "consistency"
  | `config:${string}`;

export const businessLabels: Record<BusinessChoice, string> = {
  AUTO: "自动识别",
  scrap_replacement_qingdao: "青岛报废置换审核",
  scrap_replacement_changchun: "长春报废置换审核",
  vehicle_source: "车源审核",
  transfer: "过户审核",
  consistency: "一致性审核（未配置）",
};

export function catalogBusinessLabel(identity: PageIdentityDeclaration): string {
  const legacyKey = `${identity.business_type}_${identity.region}` as BusinessChoice;
  const title = identity.title || businessLabels[legacyKey]
    || businessLabels[identity.business_type as BusinessChoice] || identity.business_type;
  if (identity.title === "报废置换审核" && identity.business_type === "scrap_replacement" && businessLabels[legacyKey])
    return businessLabels[legacyKey];
  return identity.title && identity.region !== "default" ? `${title} · ${identity.region}` : title;
}

/** The catalog is authoritative whenever available, including version 1.0 pages. */
export function catalogBusinessChoices(catalog: PageCatalog | null): Record<string, string> {
  const effective = catalog?.identities.length ? catalog : bundledPageCatalog;
  return {
    AUTO: businessLabels.AUTO,
    ...Object.fromEntries(effective.identities.map(identity => [
      `config:${encodeURIComponent(JSON.stringify({
        businessType: identity.business_type, region: identity.region,
        profileVersion: identity.profile_version || "1.0", pageId: identity.page_id,
      }))}`,
      catalogBusinessLabel(identity),
    ])),
  };
}

/** 区域后缀：业务列表里的键是「业务_区域」，查采集清单时要去掉。 */
const REGION_SUFFIXES = ["_qingdao", "_changchun", "_default"];

/** 需要向后端查询采集清单的业务类型，由业务列表推导，不另外维护。 */
export const manifestBusinessTypes: string[] = [
  ...new Set(
    Object.keys(businessLabels)
      .filter((key) => key !== "AUTO")
      .map((key) => REGION_SUFFIXES.reduce((acc, suffix) => acc.replace(suffix, ""), key)),
  ),
];

const fieldLabels: Record<string, string> = {
  "application.submitted_at": "申请时间",
  "application.owner_type": "车辆所有人类型",
  "old_vehicle.type": "报废车辆类型",
  "old_vehicle.recycle_date": "报废交车日期",
  "scrap_certificate.certificate_no": "报废证明编号",
  "application.dealer_name": "经销商",
  "old_vehicle.vin": "报废车辆车架号",
  "old_vehicle.plate_no": "报废车辆车牌号",
  "old_vehicle.owner": "报废车辆所有人",
  "old_vehicle.engine_model": "报废发动机型号",
  "new_vehicle.fuel_type": "新车燃料类型",
  "invoice.code": "发票代码",
  "invoice.invoice_no": "发票号码",
  "invoice.amount": "开票金额",
  "invoice.invoice_date": "开票日期",
  "new_vehicle.vin": "新车车架号",
  "page_ocr.new_vehicle_vin": "OCR新车车架号",
  "new_vehicle.plate_no": "新车车牌号",
  "new_vehicle.owner": "新车所有人",
  "new_vehicle.registration_date": "注册日期",
  "application.terminal_certificate_no": "终端证件号",
  "application.customer_name": "客户名称",
  "application.terminal_phone": "终端客户手机号",
};

export function manualBusinessSelection(
  choice: Exclude<BusinessChoice, "AUTO">,
): BusinessSelection {
  if (choice.startsWith("config:")) {
    const encoded = choice.slice("config:".length);
    // One encoded payload preserves page identity, including colons in page_id.
    try {
      const parsed = JSON.parse(decodeURIComponent(encoded)) as { businessType?: string; region?: string; profileVersion?: string; pageId?: string };
      if (parsed.businessType && parsed.region) {
        return {
          businessType: parsed.businessType as BusinessType,
          region: parsed.region as BusinessSelection["region"],
          profileVersion: parsed.profileVersion || "1.0",
          workflowStage: parsed.businessType as BusinessType,
          selectionMode: "MANUAL",
          ...(parsed.pageId ? { pageId: parsed.pageId } : {}),
        };
      }
    } catch { /* Report invalid catalog metadata below. */ }
    throw new Error("页面配置标识无效，请重新选择审核页面");
  }
  const region = choice.endsWith("_qingdao")
    ? "qingdao"
    : choice.endsWith("_changchun")
      ? "changchun"
      : "default";
  const businessType: BusinessType = choice.startsWith("scrap_replacement")
    ? "scrap_replacement"
    : choice.startsWith("consistency")
      ? "consistency"
      : choice as BusinessType;
  return {
    businessType,
    region,
    profileVersion: "1.0",
    workflowStage: businessType,
    selectionMode: "MANUAL",
  };
}

/**
 * 字段键 → 中文标签。优先用后端清单里的声明（新增业务时只改后端声明，
 * 面板自动跟上），清单不可用时退回下面的内置表。
 */
export function fieldLabel(field: string): string {
  return manifestFieldLabels()?.[field] ?? fieldLabels[field] ?? field;
}

export function statusLabel(status: FieldComparison["status"]): string {
  switch (status) {
    case "MATCH":
      return "一致";
    case "CONFLICT":
      return "冲突";
    case "REVIEW_REQUIRED":
      return "待复核";
  }
}

export function groupStatusLabel(status?: JobStatus): string {
  switch (status) {
    case "COMPLETED":
      return "已完成";
    case "PARTIAL":
      return "部分完成";
    case "FAILED":
      return "处理失败";
    case "CANCELLED":
      return "已取消";
    case "RUNNING":
    case undefined:
      return "识别中";
  }
}

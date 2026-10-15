/**
 * 功能：集中维护业务、字段和状态展示标签。
 * 职责边界：不包含审核判定逻辑。
 * 修改日期：2026-08-26
 * 修改人：wuyi
 */

import { manifestFieldLabels } from "./browser/collect-manifest.ts";
import type {
  BusinessSelection,
  BusinessType,
  FieldComparison,
  JobStatus,
} from "./types/review";

export type BusinessChoice =
  | "AUTO"
  | "scrap_replacement_qingdao"
  | "scrap_replacement_changchun"
  | "consistency"
  | "transfer"
  | "vehicle_source"
  | `config:${string}`;

export const businessLabels: Record<BusinessChoice, string> = {
  AUTO: "自动识别",
  scrap_replacement_qingdao: "青岛报废置换审核",
  scrap_replacement_changchun: "长春报废置换审核",
  vehicle_source: "车源审核",
  // 一致性和过户都不分地区：青岛、长春两个页面地址用的是同一套规则，所以
  // 这里各只有一项，不带地区后缀。
  consistency: "一致性审核（未配置）",
  // 与一致性审核共用两个地址，自动识别靠页面特征文案区分；特征不全时
  // 需要审核员在这里手动选。
  transfer: "过户审核",
};

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
    // New catalog entries carry the stable page identity as one encoded payload.
    // Keep accepting the old four-part key so previously opened panels continue to work.
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
    } catch {
      // Legacy key format below.
    }
    const [, businessType, region, profileVersion, pageId] = choice.split(":");
    return {
      businessType: businessType as BusinessType,
      region: region as BusinessSelection["region"],
      profileVersion,
      workflowStage: businessType as BusinessType,
      selectionMode: "MANUAL",
      ...(pageId ? { pageId } : {}),
    };
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
      : choice.startsWith("transfer")
        ? "transfer"
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

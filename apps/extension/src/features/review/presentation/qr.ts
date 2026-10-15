/**
 * 功能：把二维码核验结果转换为界面展示数据。
 * 职责边界：不访问二维码网页或重新核验。
 * 修改日期：2026-08-26
 * 修改人：wuyi
 */

import type { QrCheck } from "../../../shared/contracts/review";

const statusTitles = {
  MATCH: "官网核验通过",
  CONFLICT: "官网字段冲突",
  REVIEW_REQUIRED: "官网核验待复核",
} as const;

export function qrCheckPresentation(check: QrCheck) {
  const fields = [
    { label: "回收证明编号", value: check.page_fields.certificate_no },
    { label: "车架号", value: check.page_fields.vin },
  ].filter((field): field is { label: string; value: string } => Boolean(field.value));
  const invalidDomain = check.domain_valid === false;

  return {
    title: invalidDomain ? "官网网址不正确" : statusTitles[check.status],
    visualStatus: invalidDomain ? "CONFLICT" : check.status,
    fields,
    message: check.message,
  };
}

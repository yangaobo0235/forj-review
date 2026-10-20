/** 当前采集声明及标签索引。内置字段仅供清单不可用时兼容采集。 */
import type { FieldDefinition } from "../dom.ts";
import { manifestFieldDefinitions } from "./collect-manifest.ts";

export const normalizeText = (value: unknown) => String(value || "")
  .replace(/[＊*]/g, "")
  .replace(/\s+/g, "")
  .replace(/[：:]$/, "");
const FIELD_DEFINITIONS: Record<string, FieldDefinition> = {
  "application.id": {
    aliases: ["申请单ID", "申请单编号"],
    section: "unknown",
  },
  "application.submitted_at": {
    aliases: ["申请时间"],
    section: "unknown",
    reviewable: false,
  },
  "application.owner_type": {
    aliases: ["车辆所有人类型"],
    section: "unknown",
  },
  "page_ocr.new_vehicle_vin": {
    aliases: ["OCR新车车架号"],
    section: "new_vehicle",
  },
  "application.customer_name": {
    aliases: ["客户名称"],
    section: "new_vehicle",
  },
  "old_vehicle.type": {
    aliases: ["报废车辆类型"],
    section: "old_vehicle",
  },
  "application.dealer_name": {
    aliases: ["经销商"],
    section: "old_vehicle",
    // 经销商由系统写死展示，不是审核详情中的可编辑/可核验控件。
    reviewable: false,
  },
  "old_vehicle.recycle_date": {
    aliases: ["报废交车日期", "报废车日期"],
    section: "old_vehicle",
  },
  "old_vehicle.vin": {
    aliases: ["报废车辆车架号", "旧车车架号", "车架号"],
    section: "old_vehicle",
    sectionRequired: true,
  },
  "old_vehicle.owner": {
    aliases: ["报废车辆所有人", "旧车所有人", "车辆所有人", "所有人"],
    section: "old_vehicle",
    sectionRequired: true,
  },
  "old_vehicle.plate_no": {
    aliases: ["报废车辆车牌号", "旧车车牌号", "车牌号"],
    section: "old_vehicle",
    sectionRequired: true,
  },
  "old_vehicle.engine_model": {
    aliases: ["报废发动机型号", "发动机型号"],
    section: "old_vehicle",
  },
  "old_vehicle.emission_standard": {
    aliases: ["排放标准"],
    section: "old_vehicle",
  },
  "old_vehicle.fuel_type": {
    aliases: ["燃料种类", "燃料类型"],
    section: "old_vehicle",
  },
  "old_vehicle.is_jiefang": {
    aliases: ["是否解放牌"],
    section: "old_vehicle",
  },
  "scrap_certificate.certificate_no": {
    aliases: ["报废证明编号", "回收证明编号"],
    section: "old_vehicle",
  },
  "new_vehicle.vin": {
    aliases: ["新车车架号", "车架号"],
    section: "new_vehicle",
    sectionRequired: true,
  },
  "new_vehicle.owner": {
    aliases: ["新车所有人", "车辆所有人", "所有人"],
    section: "new_vehicle",
    sectionRequired: true,
  },
  "new_vehicle.plate_no": {
    aliases: ["新车车牌号", "车牌号"],
    section: "new_vehicle",
    sectionRequired: true,
  },
  "invoice.code": {
    aliases: ["发票代码"],
    section: "new_vehicle",
  },
  "invoice.invoice_no": {
    aliases: ["发票号码"],
    section: "new_vehicle",
  },
  "invoice.invoice_date": {
    aliases: ["开票日期", "发票日期"],
    section: "new_vehicle",
  },
  "invoice.amount": {
    aliases: ["开票金额"],
    section: "new_vehicle",
  },
  "new_vehicle.fuel_type": {
    aliases: ["新车燃料类型"],
    section: "new_vehicle",
  },
  "new_vehicle.registration_date": {
    aliases: ["注册日期"],
    section: "new_vehicle",
  },
  "application.terminal_certificate_no": {
    aliases: ["终端证件号"],
    section: "new_vehicle",
  },
  "application.terminal_phone": {
    aliases: ["终端客户手机号"],
    section: "new_vehicle",
  },
};

/**
 * 字段定义表：后端下发了采集清单就用清单，否则用内置表兜底。
 * 清单是后端声明的唯一来源，内置表只保证清单不可用时仍能采集。
 */
export const fieldDefinitions = () => manifestFieldDefinitions() || FIELD_DEFINITIONS;
let indexedDefinitions: Record<string, FieldDefinition> | null = null;
let labelIndex = new Map<string, Array<{ field: string; definition: FieldDefinition }>>();
export const definitionsByLabel = () => {
  const definitions = fieldDefinitions();
  if (definitions !== indexedDefinitions) {
    indexedDefinitions = definitions;
    labelIndex = new Map();
    for (const [field, definition] of Object.entries(definitions)) {
      for (const label of new Set(definition.aliases.map(normalizeText))) {
        const entries = labelIndex.get(label) || [];
        entries.push({ field, definition });
        labelIndex.set(label, entries);
      }
    }
  }
  return labelIndex;
};

export const definitionForLabel = (label: string) => {
  const normalized = normalizeText(label);
  const matches = definitionsByLabel().get(normalized) || [];
  if (matches.length !== 1) return null;
  const { field, definition } = matches[0];
  return { field, ...definition };
};

export const isKnownLabel = (label: string) => {
  const normalized = normalizeText(label);
  return definitionsByLabel().has(normalized);
};

export const isSectionRequired = (field: string) => Boolean(fieldDefinitions()[field]?.sectionRequired);

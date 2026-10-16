/**
 * 页面采集清单：后端下发的字段别名、页面分组标题和材料分组关键词。
 *
 * 职责边界：只做结构转换和暂存，不判断业务。后端是这几张表的唯一来源，
 * 各模块里的内置表只作为清单不可用时的兜底；清单一旦应用就优先使用。
 */
import type { FieldDefinition } from "../dom.ts";

import type { CollectManifest } from "../../../shared/contracts/collection";
export type { CollectManifest, ManifestField, ManifestPageGroup, ManifestMaterial } from "../../../shared/contracts/collection";

export interface ManifestImageProfile {
  /** 已知的图片类型：材料类型 + 页面分组分区（非材料分区除外）。 */
  knownTypes: Set<string>;
  /** 材料分组关键词构成的正则，用于判断图片是否属于审核材料。 */
  hintsPattern: RegExp;
  /** 业务认定的材料分区，用于给图片打分。 */
  scopes: Set<string>;
  /**
   * 关键词正则 → 材料类型，按关键词长度降序。
   *
   * `hintsPattern` 把所有关键词压成一个正则，只回答"这是不是材料"，丢掉了
   * "这个关键词属于哪份材料"。判类型需要后者，所以单独留一份带归属的表。
   */
  hintTypes: Array<[RegExp, string]>;
}

const NEVER_MATCHES = /$^/;

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function buildImageProfile(manifest: CollectManifest): ManifestImageProfile {
  const hints = [...new Set(manifest.materials.flatMap((item) => item.hints))].filter(Boolean);
  return {
    knownTypes: new Set([
      ...manifest.materials.map((item) => item.document_type),
      ...manifest.page_groups.map((group) => group.scope).filter((scope) => scope !== "other"),
    ]),
    hintsPattern: hints.length ? new RegExp(hints.map(escapeRegExp).join("|")) : NEVER_MATCHES,
    scopes: new Set(manifest.scopes),
    // 最具体的关键词优先：「二手车销售统一发票」必须赢过「发票」，否则宽泛词先
    // 命中会把图片判成别的材料。和 content.ts 里"铭牌必须先于登记证书"是同一类
    // 顺序问题——那份表就踩过这个坑。
    //
    // **排除页面分组名**：`hints` 的语义是「页面分组文字」，既包含材料名也包含
    // 分区名。分区名不能用来定类型——报废置换把「旧车资料」同时声明给了回收证明
    // 和行驶证，拿它定类型会让一张只写着分区名的图从 `old_vehicle` 漂移成
    // `scrap_certificate`。定类型只用材料自己的名字。
    hintTypes: (() => {
      const groupLabels = new Set(manifest.page_groups.map((group) => group.label));
      return manifest.materials
        .flatMap((item) => item.hints
          .filter((hint) => Boolean(hint) && !groupLabels.has(hint))
          .map((hint) => [hint, item.document_type] as [string, string]))
        .sort((left, right) => right[0].length - left[0].length)
        .map(([hint, documentType]) => [
          new RegExp(escapeRegExp(hint), "i"),
          documentType,
        ] as [RegExp, string]);
    })(),
  };
}

export function toFieldDefinitions(manifest: CollectManifest): Record<string, FieldDefinition> {
  const definitions: Record<string, FieldDefinition> = {};
  for (const field of manifest.fields) {
    const definition: FieldDefinition = {
      aliases: [...field.aliases],
      locatorName: field.locator_name, locatorId: field.locator_id, occurrence: field.occurrence,
      section: field.section,
    };
    // 与内置表保持同样的写法：只在偏离默认值时写出这两个标记。
    if (!field.reviewable) definition.reviewable = false;
    if (field.section_required) definition.sectionRequired = true;
    definitions[field.key] = definition;
  }
  return definitions;
}

export function toGroupLabelEntries(
  manifest: CollectManifest,
): [string, { scope: string; title: string; occurrence?: number }][] {
  return manifest.page_groups.map((group) => [
    group.label,
    { scope: group.scope, title: group.title, ...(group.occurrence ? {occurrence: group.occurrence} : {}) },
  ]);
}

let appliedFields: Record<string, FieldDefinition> | null = null;
let appliedGroups: [string, { scope: string; title: string; occurrence?: number }][] | null = null;
let appliedImageProfile: ManifestImageProfile | null = null;
let appliedSlots: [string, string][] | null = null;
let appliedFieldLabels: Record<string, string> | null = null;
let appliedMaterialLabels: Record<string, string> | null = null;
let appliedWritableFields: Set<string> | null = null;
let appliedWritableKinds: Set<string> | null = null;
let appliedIdentityAnchors: string[] | null = null;
let appliedRecordIdentityFields: string[] | null = null;
let appliedSections: { key: string; title: string }[] = [];
let currentManifest: unknown = null;

export const currentCollectManifest = () => currentManifest;

const isValid = (manifest: unknown): manifest is CollectManifest => {
  const candidate = manifest as CollectManifest | null;
  return Boolean(
    candidate
      && Array.isArray(candidate.scopes)
      && Array.isArray(candidate.fields)
      && Array.isArray(candidate.page_groups)
      && Array.isArray(candidate.materials)
      && (candidate.record_identity_fields === undefined || (
        Array.isArray(candidate.record_identity_fields)
        && Array.isArray(candidate.identity_anchors)
        && candidate.record_identity_fields.every(field => typeof field === "string" && candidate.identity_anchors!.includes(field))
      )),
  );
};

/**
 * 应用后端下发的采集清单。结构不完整时返回 false 并清空，调用方继续用内置表。
 */
export function applyCollectManifest(manifest: unknown): boolean {
  currentManifest = manifest;
  if (!isValid(manifest)) {
    appliedFields = null;
    appliedGroups = null;
    appliedImageProfile = null;
    appliedSlots = null;
    appliedFieldLabels = null;
    appliedMaterialLabels = null;
    // Explicit identity metadata that fails validation must never enable legacy writes.
    const invalidIdentity = Boolean(manifest && typeof manifest === "object" && "record_identity_fields" in manifest);
    appliedWritableFields = invalidIdentity ? new Set() : null;
    appliedWritableKinds = invalidIdentity ? new Set() : null;
    appliedIdentityAnchors = invalidIdentity ? [] : null;
    appliedRecordIdentityFields = invalidIdentity ? [] : null;
    appliedSections = [];
    return false;
  }
  appliedFields = toFieldDefinitions(manifest);
  appliedSections = manifest.page_sections ?? [];
  appliedGroups = toGroupLabelEntries(manifest);
  appliedImageProfile = buildImageProfile(manifest);
  appliedSlots = manifest.materials.flatMap((item) =>
    (item.slots ?? []).map(
      ([scope, order]) => [`${scope}:${order}`, item.document_type] as [string, string],
    ),
  );
  appliedFieldLabels = Object.fromEntries(
    manifest.fields.map((field) => [field.key, field.label]),
  );
  appliedMaterialLabels = Object.fromEntries(
    manifest.materials.map((item) => [item.document_type, item.label]),
  );
  // 老版本后端不带这两项时保持 null：调用方退回内置表，而不是把"清单里
  // 没有"当成"业务声明了不允许"，否则旧清单会让报废置换的回填全部失效。
  appliedWritableFields = Array.isArray(manifest.writable_fields)
    ? new Set(manifest.writable_fields)
    : null;
  appliedWritableKinds = Array.isArray(manifest.writable_control_kinds)
    ? new Set(manifest.writable_control_kinds)
    : new Set();
  appliedIdentityAnchors = Array.isArray(manifest.identity_anchors)
    ? manifest.identity_anchors
    : null;
  appliedRecordIdentityFields = Array.isArray(manifest.record_identity_fields) ? manifest.record_identity_fields : null;
  return true;
}

/** 已应用的上传槽位表；没有应用清单时返回 null，调用方退回内置表。 */
export function manifestSlotEntries(): [string, string][] | null {
  return appliedSlots;
}

/** 唯一命中的原始区域标题；多个区域同时出现时保留歧义。 */
export function manifestSectionFromText(text: string): string | null {
  const matching = appliedSections.filter(section => section.title && text.includes(section.title));
  const keys = new Set(matching.map(section => section.key));
  return keys.size === 1 ? matching[0].key : null;
}

/** 已应用的图片筛选依据；没有应用清单时返回 null，调用方退回内置规则。 */
export function manifestImageProfile(): ManifestImageProfile | null {
  return appliedImageProfile;
}

/**
 * 用清单声明的材料关键词判断图片类型；没有清单或没命中时返回 null。
 *
 * 业务把「二手车发票 → used_car_invoice」这类映射声明在扩展包里，浏览器内置的
 * 关键词表既不知道也不该知道——写死在浏览器里会让每新增一个业务都要改前端代码，
 * 而且改漏不报错，只会静默判成别的材料。
 */
export function manifestHintType(text: string): string | null {
  if (!appliedImageProfile || !text) return null;
  for (const [pattern, documentType] of appliedImageProfile.hintTypes) {
    if (pattern.test(text)) return documentType;
  }
  return null;
}

/** Physical document types are declared by the backend, including new material types. */
export function manifestDocumentType(category: string): string | null {
  return appliedMaterialLabels && Object.hasOwn(appliedMaterialLabels, category) ? category : null;
}

/** 已应用的字段定义；没有应用清单时返回 null，调用方退回内置表。 */
export function manifestFieldDefinitions(): Record<string, FieldDefinition> | null {
  return appliedFields;
}

/** 已应用的页面分组标题表；没有应用清单时返回 null。 */
export function manifestGroupLabelEntries(): [string, { scope: string; title: string; occurrence?: number }][] | null {
  return appliedGroups;
}

/**
 * 已应用的字段中文标签；没有应用清单时返回 null，调用方退回内置表。
 *
 * 侧边栏和 Content Script 是两个独立的运行时，各自应用同一份清单：采集
 * 逻辑在 Content Script，展示用的中文名在侧边栏。
 */
export function manifestFieldLabels(): Record<string, string> | null {
  return appliedFieldLabels;
}

/** 已应用的材料显示名（材料类型 → 中文名）；没有应用清单时返回 null。 */
export function manifestMaterialLabels(): Record<string, string> | null {
  return appliedMaterialLabels;
}

/**
 * 已应用的写回字段白名单；没有应用清单时返回 null，调用方退回内置表。
 *
 * 写回是不可逆的页面操作，这份白名单是浏览器的硬边界：清单之外的字段
 * 一律拒绝写入，即使消息里带了字段名。
 */
export function manifestWritableFields(): Set<string> | null {
  return appliedWritableFields;
}

/**
 * 已应用的写回控件类型白名单；没有应用清单时返回 null。
 *
 * 空集合表示"业务声明了不限制"（历史口径），与 null（没有清单）是两件事：
 * 前者按清单执行，后者才退回内置表。
 */
export function manifestWritableControlKinds(): Set<string> | null {
  return appliedWritableKinds;
}

/**
 * 已应用的页面指纹锚点；没有应用清单时返回 null，调用方退回内置表。
 *
 * 锚点写死在浏览器里会让另一个业务永远拿不到指纹（车源页面上没有
 * `old_vehicle.*`），而空指纹会让页面写回和原图定位全部拒绝执行。
 */
export function manifestIdentityAnchors(): string[] | null {
  return appliedIdentityAnchors;
}

export function manifestRecordIdentityFields(): string[] | null {
  return appliedRecordIdentityFields;
}

/** 拉取某个业务的采集清单；失败返回 null，调用方退回内置表。 */
export async function fetchCollectManifest(
  baseUrl: string,
  businessType: string,
  fetcher: typeof fetch = fetch,
  context?: {region: string; profile_version?: string; page_id?: string},
): Promise<CollectManifest | null> {
  try {
    const response = await fetcher(
      `${baseUrl}/api/review/collect-manifest?business_type=${encodeURIComponent(businessType)}${context ? `&region=${encodeURIComponent(context.region)}&profile_version=${encodeURIComponent(context.profile_version || "1.0")}&page_id=${encodeURIComponent(context.page_id || "")}` : ""}`,
      { signal: AbortSignal.timeout(10_000) },
    );
    if (!response.ok) return null;
    const manifest = await response.json();
    return isValid(manifest) ? manifest : null;
  } catch {
    return null;
  }
}

/**
 * 功能：集中维护业务、字段和状态展示标签。
 * 职责边界：不包含审核判定逻辑。
 * 修改日期：2026-08-26
 */

import { manifestFieldLabels } from "../../../platform/browser/catalog/collect-manifest.ts";
import type {
  PageCatalog,
  PageIdentityDeclaration,
} from "../../../platform/browser/catalog/page-catalog.ts";
import type { BusinessSelection, BusinessType } from "../../../shared/contracts/review";

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
  const title =
    identity.title ||
    businessLabels[legacyKey] ||
    businessLabels[identity.business_type as BusinessChoice] ||
    identity.business_type;
  if (
    identity.title === "报废置换审核" &&
    identity.business_type === "scrap_replacement" &&
    businessLabels[legacyKey]
  )
    return businessLabels[legacyKey];
  return identity.title && identity.region !== "default" ? `${title} · ${identity.region}` : title;
}

/** The catalog is authoritative whenever available, including version 1.0 pages. */
export function catalogBusinessChoices(catalog: PageCatalog | null): Record<string, string> {
  const effective = catalog ?? { version: "1.0", identities: [] };
  return {
    AUTO: businessLabels.AUTO,
    ...Object.fromEntries(
      effective.identities.map((identity) => [
        `config:${encodeURIComponent(
          JSON.stringify({
            businessType: identity.business_type,
            region: identity.region,
            profileVersion: identity.profile_version || "1.0",
            pageId: identity.page_id,
          }),
        )}`,
        catalogBusinessLabel(identity),
      ]),
    ),
  };
}

export function manualBusinessSelection(
  choice: Exclude<BusinessChoice, "AUTO">,
): BusinessSelection {
  if (choice.startsWith("config:")) {
    const encoded = choice.slice("config:".length);
    // One encoded payload preserves page identity, including colons in page_id.
    try {
      const parsed = JSON.parse(decodeURIComponent(encoded)) as {
        businessType?: string;
        region?: string;
        profileVersion?: string;
        pageId?: string;
      };
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
      /* Report invalid catalog metadata below. */
    }
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
      : (choice as BusinessType);
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
 * 面板自动跟上），清单不可用时保留字段键。
 */
export function fieldLabel(field: string): string {
  return manifestFieldLabels()?.[field] ?? field;
}

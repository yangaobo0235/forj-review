/**
 * 页面识别清单：后端下发的「管理端审核页地址 → 业务、地区、页面特征文案」。
 *
 * 职责边界：只做结构转换、暂存和匹配，不判断哪个业务更重要、不猜。
 *
 * 为什么地址不够：青岛一致性审核和青岛过户审核都挂在 `/consistency-qingdao`
 * 下，URL 完全一样，只能靠页面上的特征文案区分。清单里同一个地址会出现多条
 * 声明，这正是它存在的理由。识别不出唯一答案时返回 `null`，由审核员在下拉里
 * 选——静默认错业务的后果是拿错的规则去审单子，不报错、只是结论错。
 */
import { pagePath } from "../adapters/selection.ts";
import type { BusinessSelection, BusinessType, Region } from "../types/review";

export interface PageIdentityDeclaration {
  business_type: string;
  region: string;
  profile_version?: string;
  title?: string;
  page_id?: string;
  /** 该页面的 URL 路径，可多条。 */
  paths: string[];
  /** 页面特征文案，要求**全部**出现才算命中；空数组表示不看特征。 */
  anchors: string[];
}

export interface PageCatalog {
  version: string;
  identities: PageIdentityDeclaration[];
}

let applied: PageCatalog | null = null;

function isIdentity(value: unknown): value is PageIdentityDeclaration {
  const item = value as PageIdentityDeclaration | null;
  return Boolean(item)
    && typeof item?.business_type === "string"
    && typeof item?.region === "string"
    && Array.isArray(item?.paths)
    && Array.isArray(item?.anchors);
}

function isValid(value: unknown): value is PageCatalog {
  const catalog = value as PageCatalog | null;
  return Boolean(catalog)
    && Array.isArray(catalog?.identities)
    // 空清单当没有清单，不当「什么页面都不认识」。生产环境的清单至少包含
    // 已配置的几个业务，一份空表只可能是后端出了问题；此时退回内置表比
    // 让所有页面都变成「识别不出来」更合理。
    && catalog.identities.length > 0
    && catalog.identities.every(isIdentity);
}

/** 应用后端下发的页面识别清单；传空则清空，退回内置表。 */
export function applyPageCatalog(catalog: PageCatalog | null | undefined): void {
  applied = isValid(catalog) ? catalog : null;
}

/** 当前生效的页面识别清单；不可用时返回 null。 */
export function appliedPageCatalog(): PageCatalog | null {
  return applied;
}

export async function fetchPageCatalog(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
): Promise<PageCatalog | null> {
  try {
    const response = await fetcher(`${baseUrl}/api/review/page-catalog`, { signal: AbortSignal.timeout(10_000) });
    if (!response.ok) return null;
    const catalog = await response.json();
    return isValid(catalog) ? catalog : null;
  } catch {
    return null;
  }
}

/** 路径是否命中该前缀。按完整路径段匹配，避免相似前缀互相误判。 */
function pathMatches(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

/** 页面特征是否全部出现。空特征集视为命中——那表示这条声明不看内容。 */
function anchorsPresent(anchors: string[], text: string): boolean {
  if (!anchors.length) return true;
  return anchors.every((anchor) => text.includes(anchor));
}

function toSelection(identity: PageIdentityDeclaration): BusinessSelection {
  return {
    ...(identity.page_id ? { pageId: identity.page_id } : {}),
    businessType: identity.business_type as BusinessType,
    region: identity.region as Region,
    profileVersion: identity.profile_version || "1.0",
    workflowStage: identity.business_type as BusinessType,
    selectionMode: "AUTO",
    detectionStatus: "CONFIRMED",
  };
}

/**
 * 识别过程的可见记录。
 *
 * 识别错和识别不出来都**不会报错**，只会静静地把审核交给另一个业务的规则。
 * 排查时唯一有用的信息是"哪条声明因为缺哪个词没命中"，所以把它显式算出来，
 * 由调用方写进日志和面板提示，而不是让审核员对着一个业务名猜。
 */
export interface PageMatchDiagnostics {
  path: string;
  /** 地址命中的声明，按「业务/地区」列出。 */
  byPath: string[];
  /** 特征全部命中的声明。 */
  matched: string[];
  /** 每条候选各自缺哪些特征词。 */
  missingAnchors: Record<string, string[]>;
  /** 这条地址被多个业务共用——不看内容就判不出来。 */
  shared: boolean;
}

function label(item: PageIdentityDeclaration): string {
  return `${item.business_type}/${item.region}`;
}

function missingAnchors(item: PageIdentityDeclaration, text: string): string[] {
  return item.anchors.filter((anchor) => !text.includes(anchor));
}

/** 识别过程的可读记录，用来解释"为什么认成了这个业务"。 */
export function explainPageIdentity(
  catalog: PageCatalog | null,
  url: string,
  text = "",
): PageMatchDiagnostics {
  const path = pagePath(url);
  const byPath = (catalog?.identities ?? []).filter((item) =>
    item.paths.some((route) => pathMatches(path, route)));
  const matched = byPath.filter((item) => !missingAnchors(item, text).length);
  return {
    path,
    byPath: byPath.map(label),
    matched: matched.map(label),
    missingAnchors: Object.fromEntries(
      byPath.map((item) => [label(item), missingAnchors(item, text)]),
    ),
    shared: byPath.length > 1,
  };
}

/**
 * 在清单里判定当前页面属于哪个业务；给不出唯一答案时返回 null。
 *
 * 顺序是「先地址、后特征」，不是反过来：报废置换和车源审核的地址各自唯一，
 * 页面上一旦出现别的业务的词（共享的侧边栏、跨业务公共区块）就会误判，
 * 而它们的地址本来就够用。只有地址撞在一起时才需要看内容。
 */
export function matchPageIdentity(
  catalog: PageCatalog | null,
  url: string,
  text = "",
): BusinessSelection | null {
  if (!catalog?.identities.length) return null;
  const path = pagePath(url);

  const byPath = catalog.identities.filter((item) =>
    item.paths.some((route) => pathMatches(path, route)));

  if (byPath.length === 1) return toSelection(byPath[0]);

  if (byPath.length > 1) {
    // 地址共享：靠特征在候选里筛。筛出 0 条或 2 条以上都不猜。
    const matched = byPath.filter((item) => anchorsPresent(item.anchors, text));
    return matched.length === 1 ? toSelection(matched[0]) : null;
  }

  // 地址一条都不命中（页面改版换了路径）：全表按特征兜底，同样只认唯一解。
  const byAnchor = catalog.identities.filter(
    (item) => item.anchors.length > 0 && anchorsPresent(item.anchors, text),
  );
  return byAnchor.length === 1 ? toSelection(byAnchor[0]) : null;
}

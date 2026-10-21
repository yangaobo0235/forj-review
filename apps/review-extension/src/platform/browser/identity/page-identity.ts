import { manifestIdentityAnchors, manifestRecordIdentityFields } from "../catalog/collect-manifest.ts";
/** Page identity primitives shared by collection and write guards. */

/** 强锚点：能唯一标识记录的编号类字段；只有它们存在时指纹才算数。 */
export const isStrongIdentityAnchor = (field: string, declared?: readonly string[]) =>
  declared?.includes(field) ?? false;

/**
 * 用业务锚点拼出页面指纹。
 *
 * 指纹只收**有值的**锚点：空字段写进去只会让指纹随页面渲染时机抖动。
 * 一个强锚点都没有时返回空串——空指纹会让所有页面写回和原图定位拒绝执行，
 * 这比拿"所有人姓名"这类弱字段充当身份安全。
 *
 * 锚点必须由业务声明：写死其他业务的字段名会导致空指纹。
 */
export function buildPageFingerprint(
  fields: Record<string, unknown>,
  anchors: readonly string[] = manifestIdentityAnchors() ?? [],
  recordIdentityFields: readonly string[] = manifestRecordIdentityFields() ?? [],
): string {
  const present = anchors
    .map((field) => [field, String(fields?.[field] ?? "").trim()] as const)
    .filter(([, value]) => value);
  return present.some(([field]) => isStrongIdentityAnchor(field, recordIdentityFields)) ? JSON.stringify(present) : "";
}

/** A changed target cannot vouch for itself: require an unchanged independent record identifier. */
export function sameRecordExceptFields(
  expectedFingerprint: string,
  actualFingerprint: string,
  excludedFields: readonly string[],
  recordIdentityFields: readonly string[] = manifestRecordIdentityFields() ?? [],
): boolean {
  const parse = (value: string): Array<[string, string]> | null => {
    try {
      const entries: unknown = JSON.parse(value);
      if (!Array.isArray(entries) || entries.some(item => !Array.isArray(item) || item.length !== 2
        || typeof item[0] !== "string" || typeof item[1] !== "string")) return null;
      return entries as Array<[string, string]>;
    } catch { return null; }
  };
  const expected = parse(expectedFingerprint);
  const actual = parse(actualFingerprint);
  if (!expected || !actual) return false;
  const actualMap = new Map(actual);
  const expectedMap = new Map(expected);
  // Adding/removing an independent identity anchor also invalidates the snapshot.
  if (actual.some(([key]) => !excludedFields.includes(key) && !expectedMap.has(key))) return false;
  let stableIdentifier = false;
  for (const [key, value] of expected) {
    if (excludedFields.includes(key)) continue;
    if (!actualMap.has(key) || actualMap.get(key) !== value) return false;
    if (value && isStrongIdentityAnchor(key, recordIdentityFields)) stableIdentifier = true;
  }
  return stableIdentifier;
}

export function createPageInstanceId(): string {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
}

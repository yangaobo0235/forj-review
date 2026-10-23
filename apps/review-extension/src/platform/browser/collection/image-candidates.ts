import { manifestImageProfile } from "../catalog/collect-manifest.ts";

export interface ImageCandidate {
  index: number;
  src: string;
  visible: boolean;
  ariaHidden?: boolean;
  businessScope: string;
  role?: string | null;
  emptySlot?: boolean;
  className?: string;
  naturalWidth?: number;
  naturalHeight?: number;
  categoryHint: string;
  hint?: string;
  groupTitle?: string;
  ignoredMaterial?: boolean;
}

/**
 * 功能：过滤、评分并覆盖选择业务材料图片。
 * 职责边界：只选择候选，不读取或压缩图片字节。
 * 修改日期：2026-08-26
 */

const decorativePattern = /(?:^|[-_\s])(logo|icon|avatar|favicon|badge|spinner)(?:$|[-_\s])/i;
const emptyTypes = new Set<string>();
const neverMatches = /$^/;
const imageProfile = () => manifestImageProfile();
const knownTypesFor = () => imageProfile()?.knownTypes ?? emptyTypes;
const hintsPatternFor = () => imageProfile()?.hintsPattern ?? neverMatches;
const scopesFor = () => imageProfile()?.scopes ?? emptyTypes;

const eligible = (candidate: ImageCandidate) => {
  if (!candidate.src || !candidate.visible || candidate.ariaHidden || candidate.ignoredMaterial)
    return false;
  if (candidate.businessScope === "other") return false;
  if (candidate.role === "presentation" || candidate.emptySlot) return false;
  if (decorativePattern.test(candidate.className || "")) return false;
  const width = Number(candidate.naturalWidth || 0);
  const height = Number(candidate.naturalHeight || 0);
  // 三个条件任一成立即入选，彼此独立：
  //   - 类型已知：槽位已经认定它是材料；
  //   - 命中材料关键词：分组标题写着「铭牌」「行驶证」等，就是材料；
  //   - 面积够大：类型和标题都判断不了时，用尺寸兜底。
  // 面积兜底**不能**反过来否决前两条：小尺寸的材料图（例如车辆铭牌）
  // 会被挡在重读之前，等于连补救机会都没有。
  const isKnownType = knownTypesFor().has(candidate.categoryHint);
  const matchedHint = hintsPatternFor().test(candidate.hint || "");
  return isKnownType || matchedHint || width * height >= 40_000;
};

const score = (candidate: ImageCandidate) => {
  const scopeScore = scopesFor().has(candidate.businessScope) ? 10_000_000 : 0;
  const knownScore = knownTypesFor().has(candidate.categoryHint) ? 1_000_000 : 0;
  const businessScore = hintsPatternFor().test(candidate.hint || "") ? 100_000 : 0;
  const areaScore = Math.min(
    Number(candidate.naturalWidth || 0) * Number(candidate.naturalHeight || 0),
    99_999,
  );
  return scopeScore + knownScore + businessScore + areaScore;
};

const coverageKey = (candidate: ImageCandidate) =>
  `${candidate.businessScope || "unknown"}::${candidate.categoryHint || "unknown"}::${candidate.groupTitle || ""}`;

const select = <T extends ImageCandidate>(candidates: T[], limit = 10) => {
  const ranked = candidates
    .filter(eligible)
    .map((candidate) => ({ candidate, rank: score(candidate) }))
    .sort((left, right) => right.rank - left.rank || left.candidate.index - right.candidate.index)
    .map((item) => item.candidate);
  const reservedByBucket = new Map<string, T>();
  for (const candidate of ranked) {
    const key = coverageKey(candidate);
    if (!reservedByBucket.has(key)) reservedByBucket.set(key, candidate);
  }
  const selectedCandidates = new Set(Array.from(reservedByBucket.values()).slice(0, limit));
  for (const candidate of ranked) {
    if (selectedCandidates.size >= limit) break;
    selectedCandidates.add(candidate);
  }
  return {
    selected: ranked.filter((candidate) => selectedCandidates.has(candidate)).slice(0, limit),
    overflow: ranked.length > limit,
    scannedCount: candidates.length,
    eligibleCount: ranked.length,
  };
};

export const ReviewImageCandidates = { select };

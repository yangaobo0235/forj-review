/** 将已选中的图片节点转换为审核请求元数据；不下载图片。 */
import type { PageImage } from "../../../shared/contracts/review.ts";
import { classifyImage, imageDocumentType, identitySidesForLabel } from "./material-hints.ts";

type ImageSelection = { image: HTMLImageElement; index: number; imageId?: string; businessScope: string; groupOrder?: number; categoryHint?: string; groupTitle?: string; labelHint?: string };

export const imageAssetMetadata = (candidate: ImageSelection): PageImage => {
  const { image, index } = candidate;
  const src = image.currentSrc || image.src;
  const hint = candidate.labelHint || image.alt || image.title || candidate.groupTitle || "";
  const categoryHint = candidate.categoryHint || classifyImage(hint);
  return {
    imageId: candidate.imageId,
    index,
    src,
    alt: image.alt || "",
    group: hint.slice(0, 80) || "未分类",
    categoryHint,
    // Use the image's own card label before positional slot fallback. This lets
    // multi-material sections bypass model
    // classification when their per-image labels are explicit.
    documentTypeHint: imageDocumentType({ ...candidate, categoryHint }),
    identitySidesHint: identitySidesForLabel(candidate.labelHint || candidate.groupTitle || image.alt || ""),
    businessScope: candidate.businessScope,
    groupTitle: candidate.groupTitle,
    groupOrder: candidate.groupOrder,
    pagePosition: `${Math.round(image.getBoundingClientRect().left)},${Math.round(image.getBoundingClientRect().top)}`,
    naturalWidth: image.naturalWidth || 0,
    naturalHeight: image.naturalHeight || 0,
    collectionError: /^https?:\/\//i.test(src) ? null : "这份材料暂时无法读取，请刷新原页面后重新审核；仍失败请查看原图人工核对",
  };
};


import { ReviewBusinessScope } from "./business-scope.ts";
import { imageLabelFor, nearestAncestorText } from "./image-label.ts";

/** Shared DOM image discovery for formal collection and candidate trials. */
export function collectImageCandidates(classifyImage: (hint: string) => string) {
  const pageImages = Array.from(document.images);
  const imageIndexes = new Map(pageImages.map((image, index) => [image, index]));
  const scopeItems: Array<{ kind: "label"; text: string } | { kind: "image"; index: number }> = []
  document.body.querySelectorAll("*").forEach((element) => {
    const directText = Array.from(element.childNodes)
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent || "")
      .join(" ")
      .trim();
    if (ReviewBusinessScope.scopeForLabel(directText)) {
      scopeItems.push({ kind: "label", text: directText });
    }
    if (element instanceof HTMLImageElement) {
      const index = imageIndexes.get(element);
      if (index != null) scopeItems.push({ kind: "image", index });
    }
  });
  const scopeByIndex = new Map(
    ReviewBusinessScope.assign(scopeItems).map((item) => [item.index, item])
  );

  const imageCandidates = pageImages.map((image, index) => {
    const rect = image.getBoundingClientRect();
    const style = window.getComputedStyle(image);
    const labelHint = imageLabelFor(image);
    // 旧写法 `image.closest("section, article, li, div")?.textContent` 会停在
    // 只包着 img 的空壳上，拿到空字符串，小标题和分组文本一起丢掉。改成向上找
    // 最近的非空祖先文本。
    const hint = labelHint || nearestAncestorText(image) || image.alt || "";
    const ownHint = [image.alt, image.title, image.getAttribute("aria-label")]
      .filter(Boolean)
      .join(" ");
    const parent = image.parentElement;
    const emptySlot = /暂无图片|尚未上传|未上传图片/.test(ownHint)
      || Boolean(
        parent
        && parent.querySelectorAll("img").length === 1
        && /暂无图片|尚未上传|未上传图片/.test(parent.textContent || ""),
      );
    const scope = scopeByIndex.get(index) || {
      businessScope: "unknown",
      groupTitle: "未分类资料",
      groupOrder: index + 1,
      imageId: `unknown-${String(index + 1).padStart(2, "0")}`,
    };
    return {
      image,
      index,
      ...scope,
      src: image.currentSrc || image.src,
      visible: rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden",
      naturalWidth: image.naturalWidth || 0,
      naturalHeight: image.naturalHeight || 0,
      className: typeof image.className === "string" ? image.className : "",
      role: image.getAttribute("role") || "",
      ariaHidden: image.getAttribute("aria-hidden") === "true",
      emptySlot,
      hint,
      categoryHint: classifyImage(hint),
      labelHint,
    };
  });
  return imageCandidates;
}

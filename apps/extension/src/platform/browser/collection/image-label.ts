/**
 * 读取与图片配对的小标题（材料名）。
 *
 * 职责边界：只做 DOM 定位，不判材料类型。类型判定归 `collect-manifest.ts` 的
 * 清单关键词和 `content.ts` 的内置兜底表——在这里写业务正则会让页面改文案时
 * 静默判错，而且和后端清单形成第二份材料表。
 */
import { manifestHintType } from "../catalog/collect-manifest.ts";

const MAX_LABEL_CHARS = 40;
const MAX_WALK_DEPTH = 8;

const collapse = (text: string | null | undefined): string =>
  (text || "").replace(/\s+/g, " ").trim();

/**
 * 取图片的小标题。
 *
 * 不能用 `image.closest("div").textContent`：最近的那个 div 往往只是包着 img 的
 * 空壳（预览容器），文字为空；而再往上的分组容器又同时装着好几张图和好几个标题，
 * 取到的是一整段拼接文本。两者的表现都是"判不出类型"，只是原因相反。
 *
 * 判别条件用结构而不是类名：**恰好只含这一张 img、且文字不长**的最近祖先就是
 * 图片卡片。空壳因为没文字被跳过，分组因为含多张 img 被跳过，页面改 class 名
 * 也不影响。
 */
export function imageLabelFor(image: HTMLImageElement): string {
  const body = image.ownerDocument?.body ?? null;
  let node: Element | null = image.parentElement;
  for (let depth = 0; node && node !== body && depth < MAX_WALK_DEPTH; depth += 1) {
    if (node.querySelectorAll("img").length === 1) {
      const text = collapse(node.textContent);
      if (text) return text.length <= MAX_LABEL_CHARS ? text : "";
    }
    node = node.parentElement;
  }
  return "";
}

/**
 * 取最近的非空祖先文本，作为小标题缺失时的兜底。
 *
 * 与 `imageLabelFor` 的区别是不要求"只含一张图"：它可能拿到分组级文本，但至少
 * 不会是空壳的空字符串——旧实现 `closest(...).textContent` 在空壳上返回 ""，
 * 等于把有用的上层文本也一起丢掉了。
 */
export function nearestAncestorText(image: HTMLImageElement, limit = 160): string {
  const body = image.ownerDocument?.body ?? null;
  let node: Element | null = image.parentElement;
  for (let depth = 0; node && node !== body && depth < MAX_WALK_DEPTH; depth += 1) {
    const text = collapse(node.textContent);
    if (text) return text.slice(0, limit);
    node = node.parentElement;
  }
  return "";
}

/** 小标题 → 材料类型，只查清单声明；查不到返回 null 交给调用方继续兜底。 */
export function documentTypeForImageLabel(label: string): string | null {
  if (!label) return null;
  return manifestHintType(label);
}

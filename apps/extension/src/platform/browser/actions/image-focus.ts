
/**
 * 功能：把审核页面上的原图滚动到可视区域并短暂高亮。
 * 职责边界：只操作传入的 <img> 及其可见容器，不做图片字节处理、不改审核状态。
 * 修改日期：2026-09-20
 * 修改人：wuyi
 */

  // 缩略图本身可能只有一百来像素宽，只给 <img> 描边在长列表里几乎看不见。
  // 容器一起描边：审核员看到的是"这一张"，而不是"某处有根蓝线"。
  const HIGHLIGHT_CONTAINER_SELECTOR = "figure, .ant-image, .ant-upload-list-item, .el-upload-list__item, li, [class*='thumb'], [class*='Thumb']";

  const highlightContainer = (image: HTMLElement) => {
    const container = image.closest?.(HIGHLIGHT_CONTAINER_SELECTOR);
    // 容器选择器很宽，命中 body 级别的容器时不如不描——描出来是整页蓝框。
    if (!container || container === image || container.tagName === "BODY" || container.tagName === "HTML") {
      return null;
    }
    return container as HTMLElement;
  };

  const outline = (element: HTMLElement, color: string, offset: string) => {
    element.style.outline = color;
    element.style.outlineOffset = offset;
  };

  const focus = (image: HTMLElement | null, schedule?: (callback: () => void, delay: number) => unknown) => {
    if (!image?.isConnected) return { ok: false, error: "原图已变化，请重新采集" };
    const scheduler = schedule || globalThis.setTimeout.bind(globalThis);
    const container = highlightContainer(image);
    (container || image).scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    const previous = [
      [image, image.style.outline, image.style.outlineOffset],
      ...(container ? [[container, container.style.outline, container.style.outlineOffset]] : []),
    ] as Array<[HTMLElement, string, string]>;
    previous.forEach(([element]) => outline(element, "4px solid #1677ff", "3px"));
    // 页面自己处理缩略图点击（打开预览）。点在图片上，事件照常冒泡到容器。
    image.click();
    scheduler(() => {
      previous.forEach(([element, outlineValue, offsetValue]) => {
        element.style.outline = outlineValue;
        element.style.outlineOffset = offsetValue;
      });
    }, 2000);
    return { ok: true };
  };

  export const ReviewImageFocus = { focus };

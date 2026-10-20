import { JSDOM } from "jsdom";
import { onTestFinished } from "vitest";

/** A native DOM fixture for collection/write integration tests; jsdom has no layout engine. */
export function domReviewTabs(labels = ["初审", "复审"], selected = 0) {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {url: "https://admin.forjtruck.com/consistency-changchun?showPageModel=1"});
  onTestFinished(() => dom.window.close());
  const { document, HTMLElement } = dom.window;
  HTMLElement.prototype.getClientRects = function () {
    return this.hidden || this.style.display === "none" ? [] : [{width: 100, height: 20}];
  };
  Object.defineProperty(HTMLElement.prototype, "innerText", {get() {return this.textContent;}});
  const add = (parent, tag, attrs = {}, text = "") => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    node.textContent = text;
    parent.append(node);
    return node;
  };
  const form = add(document.body, "form");
  const list = add(form, "div", {role: "tablist"});
  const tabs = labels.map((label, i) => add(list, "div", {role: "tab", id: `tab-${i}`,
    "aria-controls": `panel-${i}`}, label));
  const panels = labels.map((label, i) => add(form, "div", {role: "tabpanel", id: `panel-${i}`,
    "aria-labelledby": `tab-${i}`}, label));
  const select = index => {
    tabs.forEach((tab, i) => tab.setAttribute("aria-selected", String(i === index)));
    panels.forEach((panel, i) => panel.setAttribute("aria-hidden", String(i !== index)));
  };
  select(selected);
  return {document, form, list, tabs, panels, add, select};
}
